#!/usr/bin/env python3
"""Read-only check of a Kubernetes cluster for an Onehouse Self-Managed Kubernetes data plane.

Prints one JSON document: the checks, capacity per role, what to add when it falls short, and a
proposed node-label plan. Rules: references/requirements.md and references/node-labelling.md.

    check_cluster.py --context CTX [--kubeconfig PATH]

Exit 0 when every check passes (labels may still need applying), 1 when one fails, 2 when the
cluster can't be read.
"""
import argparse
import json
import math
import re
import shlex
import subprocess
import sys
from collections import Counter

ESSENTIAL_LABELS = ("oh-essential-pods", "oh-essential-pods-vpc-cni", "monitoring-pods")
WORKER_LABELS = ("driver-pods", "job-workers", "sql-workers")
REPAIR_KEY, REPAIR_VALUE = "pool", "job-workers-repair"
INSTANCE_TYPE = "oh-instance-type"
INSTANCE_TYPE_RE = re.compile(r"^[a-z0-9]([a-z0-9.-]{0,61}[a-z0-9])?$")
PROVIDER_TYPE_LABELS = ("node.kubernetes.io/instance-type", "beta.kubernetes.io/instance-type")
MEMORY_TOLERANCE = 0.02
CPU_SLACK = 0.5  # allocatable CPU sits a little under capacity on every distribution

ESSENTIAL_MIN_NODES, ESSENTIAL_MIN_CPU, ESSENTIAL_MIN_GIB = 2, 4, 20
WORKER_MIN_CPU, WORKER_MIN_GIB = 4, 10
REPAIR_MIN_CPU, REPAIR_MIN_GIB = 4, 12
# A 4 vCPU / 16 GiB node usually has about 12 GiB allocatable.
SUGGESTED = {
    "essential": ("4 vCPU / 16 GiB", 12),
    "workers": ("4 vCPU / 16 GiB or larger", 12),
    "repair": ("4 vCPU / 16 GiB", 12),
}
ROLE_LABELS = {
    "essential": {k: "true" for k in ESSENTIAL_LABELS},
    "workers": {k: "true" for k in WORKER_LABELS},
    "repair": {REPAIR_KEY: REPAIR_VALUE},
    "openengine": {},
}
UNITS = {"Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "Ti": 2**40, "K": 10**3, "M": 10**6, "G": 10**9, "T": 10**12}


def gib(quantity):
    q = str(quantity)
    for suffix, factor in UNITS.items():
        if q.endswith(suffix):
            return float(q[: -len(suffix)]) * factor / 2**30
    return float(q) / 2**30


def cpu(quantity):
    q = str(quantity)
    return float(q[:-1]) / 1000 if q.endswith("m") else float(q)


def node_info(raw):
    labels = raw["metadata"].get("labels") or {}
    spec, status = raw.get("spec") or {}, raw.get("status") or {}
    taints = [t for t in spec.get("taints") or [] if t.get("effect") in ("NoSchedule", "NoExecute")]
    ready = any(c.get("type") == "Ready" and c.get("status") == "True" for c in status.get("conditions") or [])
    return {
        "name": raw["metadata"]["name"],
        "labels": labels,
        "ready": ready,
        "schedulable": ready and not spec.get("unschedulable") and not taints,
        "cap_cpu": int(round(cpu(status["capacity"]["cpu"]))),
        "cap_gib": gib(status["capacity"]["memory"]),
        "alloc_cpu": cpu(status["allocatable"]["cpu"]),
        "alloc_gib": gib(status["allocatable"]["memory"]),
    }


def existing_roles(node):
    labels = node["labels"]
    roles = []
    if labels.get(REPAIR_KEY) == REPAIR_VALUE:
        roles.append("repair")
    if any(k in labels for k in ESSENTIAL_LABELS):
        roles.append("essential")
    if any(k in labels for k in WORKER_LABELS):
        roles.append("workers")
    if any(k.startswith("openengine-") for k in labels):
        roles.append("openengine")
    return roles


def fits(node, min_cpu, min_gib=0):
    return node["alloc_cpu"] >= min_cpu - CPU_SLACK and node["alloc_gib"] >= min_gib


def group_shapes(nodes):
    """Groups nodes by hardware shape: equal vCPU, memory within 2 %."""
    shapes = []
    for n in sorted(nodes, key=lambda n: (n["cap_cpu"], n["cap_gib"])):
        for s in shapes:
            if s["vcpu"] == n["cap_cpu"] and abs(s["ref_gib"] - n["cap_gib"]) <= MEMORY_TOLERANCE * max(s["ref_gib"], n["cap_gib"]):
                s["nodes"].append(n)
                break
        else:
            shapes.append({"vcpu": n["cap_cpu"], "ref_gib": n["cap_gib"], "nodes": [n]})
    return shapes


def sanitize(value):
    v = re.sub(r"[^a-z0-9.-]+", "-", value.lower()).strip(".-")[:63].strip(".-")
    return v if INSTANCE_TYPE_RE.match(v) else ""


def name_shapes(shapes):
    """Picks one oh-instance-type per shape, reusing valid existing values where they fit."""
    carriers = {}
    for i, s in enumerate(shapes):
        for n in s["nodes"]:
            v = n["labels"].get(INSTANCE_TYPE, "")
            if INSTANCE_TYPE_RE.match(v):
                carriers.setdefault(v, Counter())[i] += 1
    owner = {v: c.most_common(1)[0][0] for v, c in carriers.items()}
    taken = set(owner)
    for i, s in enumerate(shapes):
        own = Counter(n["labels"].get(INSTANCE_TYPE) for n in s["nodes"]
                      if owner.get(n["labels"].get(INSTANCE_TYPE)) == i)
        if own:
            s["name"], s["name_source"] = own.most_common(1)[0][0], "existing"
            continue
        provider = {sanitize(next((n["labels"][k] for k in PROVIDER_TYPE_LABELS if n["labels"].get(k)), ""))
                    for n in s["nodes"]}
        candidate = provider.pop() if len(provider) == 1 else ""
        if candidate and candidate not in taken:
            s["name"], s["name_source"] = candidate, "provider-instance-type"
        else:
            base = f"oh-{s['vcpu']}cpu-{round(max(n['cap_gib'] for n in s['nodes']))}gi"
            candidate, k = base, 2
            while candidate in taken:
                candidate, k = f"{base}-{k}", k + 1
            s["name"], s["name_source"] = candidate, "derived"
        taken.add(s["name"])
    return owner


def assign_roles(nodes, warnings):
    """Returns {node name: [roles]}: existing role labels kept, unlabelled nodes filled in."""
    roles = {n["name"]: existing_roles(n) for n in nodes}
    for n in nodes:
        if len(set(roles[n["name"]]) - {"openengine"}) > 1:
            warnings.append(f"node {n['name']} carries labels for several roles ({', '.join(roles[n['name']])}); left as is")
        if roles[n["name"]] and not n["schedulable"]:
            warnings.append(f"node {n['name']} has Onehouse role labels but is not Ready or has a NoSchedule taint")
    pre_labelled = any(roles.values())
    free = sorted((n for n in nodes if n["schedulable"] and not roles[n["name"]]),
                  key=lambda n: (n["alloc_gib"], n["alloc_cpu"]))

    def members(role):
        return [n for n in nodes if role in roles[n["name"]] and n["schedulable"]]

    def take(pred):
        for n in free:
            if pred(n):
                free.remove(n)
                return n
        return None

    if not any(fits(n, REPAIR_MIN_CPU, REPAIR_MIN_GIB) for n in members("repair")):
        n = take(lambda n: fits(n, REPAIR_MIN_CPU, REPAIR_MIN_GIB) and REPAIR_KEY not in n["labels"]) \
            or take(lambda n: fits(n, REPAIR_MIN_CPU, REPAIR_MIN_GIB))
        if n:
            roles[n["name"]] = ["repair"]

    def essential_short():
        ess = [n for n in members("essential") if fits(n, ESSENTIAL_MIN_CPU)]
        return len(ess) < ESSENTIAL_MIN_NODES or sum(n["alloc_gib"] for n in ess) < ESSENTIAL_MIN_GIB

    while essential_short():
        n = take(lambda n: fits(n, ESSENTIAL_MIN_CPU))
        if not n:
            break
        roles[n["name"]] = ["essential"]

    if not any(fits(n, WORKER_MIN_CPU, WORKER_MIN_GIB) for n in members("workers")):
        n = take(lambda n: fits(n, WORKER_MIN_CPU, WORKER_MIN_GIB))
        if n:
            roles[n["name"]] = ["workers"]
    if not pre_labelled:
        while (n := take(lambda n: fits(n, WORKER_MIN_CPU))):
            roles[n["name"]] = ["workers"]
    for n in free:
        if not pre_labelled and not fits(n, WORKER_MIN_CPU):
            warnings.append(f"node {n['name']} has fewer than {WORKER_MIN_CPU} vCPU allocatable; left unlabelled")
    return roles


def capacity(nodes, roles):
    def members(role):
        return [n for n in nodes if role in roles[n["name"]] and n["schedulable"]]

    ess = [n for n in members("essential") if fits(n, ESSENTIAL_MIN_CPU)]
    ess_gib = sum(n["alloc_gib"] for n in ess)
    work = [n for n in members("workers") if fits(n, WORKER_MIN_CPU, WORKER_MIN_GIB)]
    rep = [n for n in members("repair") if fits(n, REPAIR_MIN_CPU, REPAIR_MIN_GIB)]
    report = {
        "essential": {"nodes": len(ess), "allocatable_gib": round(ess_gib, 1),
                      "ok": len(ess) >= ESSENTIAL_MIN_NODES and ess_gib >= ESSENTIAL_MIN_GIB,
                      "need": f">= {ESSENTIAL_MIN_NODES} nodes, >= {ESSENTIAL_MIN_CPU} vCPU each, >= {ESSENTIAL_MIN_GIB} GiB allocatable in total"},
        "monitoring": {"nodes": len(ess), "ok": len(ess) >= 1, "need": ">= 1 node (carried by the essential nodes)"},
        "workers": {"nodes": len(work), "ok": len(work) >= 1,
                    "need": f">= 1 node with >= {WORKER_MIN_CPU} vCPU and >= {WORKER_MIN_GIB} GiB allocatable"},
        "repair": {"nodes": len(rep), "ok": len(rep) >= 1,
                   "need": f"1 node with >= {REPAIR_MIN_CPU} vCPU and >= {REPAIR_MIN_GIB} GiB allocatable"},
    }
    add = []
    if not report["essential"]["ok"]:
        shape, per_node = SUGGESTED["essential"]
        count = max(ESSENTIAL_MIN_NODES - len(ess), math.ceil(max(ESSENTIAL_MIN_GIB - ess_gib, 0) / per_node))
        add.append({"role": "essential", "add_nodes": count, "min_vcpu": ESSENTIAL_MIN_CPU,
                    "missing_allocatable_gib": round(max(ESSENTIAL_MIN_GIB - ess_gib, 0), 1),
                    "suggested_shape": shape, "labels": ROLE_LABELS["essential"]})
    for role, min_cpu, min_gib in (("workers", WORKER_MIN_CPU, WORKER_MIN_GIB), ("repair", REPAIR_MIN_CPU, REPAIR_MIN_GIB)):
        if not report[role]["ok"]:
            add.append({"role": role, "add_nodes": 1, "min_vcpu": min_cpu, "min_allocatable_gib": min_gib,
                        "suggested_shape": SUGGESTED[role][0], "labels": ROLE_LABELS[role]})
    return report, add


def label_plan(nodes, roles, shapes, owner, context, kubeconfig=None):
    shape_of = {n["name"]: (i, s) for i, s in enumerate(shapes) for n in s["nodes"]}
    changes = []
    for n in nodes:
        node_roles = roles[n["name"]]
        if not node_roles:
            continue
        i, shape = shape_of[n["name"]]
        wanted = {}
        for role in node_roles:
            wanted.update(ROLE_LABELS[role])
        current_type = n["labels"].get(INSTANCE_TYPE, "")
        if not (INSTANCE_TYPE_RE.match(current_type) and owner.get(current_type) == i):
            wanted[INSTANCE_TYPE] = shape["name"]
        to_set, conflicts = {}, []
        for key, value in wanted.items():
            current = n["labels"].get(key)
            if current is None:
                to_set[key] = value
            elif current != value:
                conflicts.append({"label": key, "current": current, "proposed": value})
        if "repair" in node_roles and "workers" not in node_roles and "job-workers" in n["labels"]:
            conflicts.append({"label": "job-workers", "current": n["labels"]["job-workers"], "proposed": None})
        if to_set or conflicts:
            changes.append({"node": n["name"], "roles": node_roles, "instance_type": shape["name"],
                            "set": to_set, "conflicts": conflicts})
    kubectl = (["kubectl"] + (["--kubeconfig", kubeconfig] if kubeconfig else [])
               + (["--context", context] if context else []))
    commands = [shlex.join(kubectl + ["label", "node", c["node"]] + [f"{k}={v}" for k, v in c["set"].items()])
                for c in changes if c["set"]]
    overwrites = [shlex.join(kubectl + ["label", "node", c["node"], "--overwrite"]
                             + [f"{x['label']}={x['proposed']}" if x["proposed"] else f"{x['label']}-" for x in c["conflicts"]])
                  for c in changes if c["conflicts"]]
    return {
        "node_types": [{"oh_instance_type": s["name"], "source": s["name_source"], "vcpu": s["vcpu"],
                        "memory_gib": round(max(n["cap_gib"] for n in s["nodes"]), 1),
                        "nodes": sorted(n["name"] for n in s["nodes"] if roles[n["name"]])}
                       for s in shapes if any(roles[n["name"]] for n in s["nodes"])],
        "assignments": {n["name"]: roles[n["name"]] for n in nodes},
        "changes": changes,
        "commands": commands,
        "conflict_commands": overwrites,
        "complete": not changes,
    }


def evaluate(version, admin, raw_nodes, storage_classes, context=None, kubeconfig=None):
    checks, warnings = [], []

    def check(name, ok, detail):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    check("api_server", True, f"reachable, Kubernetes {version or '?'}")
    check("cluster_admin", admin == "yes", f"kubectl auth can-i '*' '*' -A: {admin or 'no'}")
    nodes = [node_info(n) for n in raw_nodes]
    ready = [n for n in nodes if n["ready"]]
    check("nodes_ready", bool(ready), f"{len(ready)}/{len(nodes)} nodes Ready")
    defaults = sorted(s["metadata"]["name"] for s in storage_classes
                      if (s["metadata"].get("annotations") or {}).get("storageclass.kubernetes.io/is-default-class") == "true")
    check("default_storage_class", len(defaults) == 1,
          defaults[0] if len(defaults) == 1 else f"need exactly one default StorageClass, found {defaults or 'none'}")

    roles = assign_roles(ready, warnings)
    shapes = group_shapes([n for n in ready if roles[n["name"]]] or ready)
    owner = name_shapes(shapes)
    report, add = capacity(ready, roles)
    for role, r in report.items():
        check(f"capacity_{role}", r["ok"], f"{r['nodes']} node(s); need {r['need']}")
    plan = label_plan(ready, roles, shapes, owner, context, kubeconfig)
    return {
        "ok": all(c["ok"] for c in checks),
        "context": context,
        "kubeconfig": kubeconfig,
        "default_storage_class": defaults[0] if len(defaults) == 1 else None,
        "checks": checks,
        "capacity": report,
        "add_nodes": add,
        "label_plan": plan,
        "warnings": warnings,
    }


def kubectl(args, kubeconfig=None, context=None):
    cmd = ["kubectl", "--request-timeout=20s"]
    if kubeconfig:
        cmd += ["--kubeconfig", kubeconfig]
    if context:
        cmd += ["--context", context]
    return subprocess.run(cmd + list(args), capture_output=True, text=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--context", required=True, help="the context the user named; never the current one")
    parser.add_argument("--kubeconfig")
    args = parser.parse_args(argv)

    def run(*a, check=True):
        p = kubectl(a, args.kubeconfig, args.context)
        if check and p.returncode != 0:
            err = p.stderr.strip().splitlines()
            raise RuntimeError(err[-1] if err else f"kubectl {a[0]} failed")
        return p

    try:
        version = json.loads(run("version", "-o", "json").stdout).get("serverVersion", {}).get("gitVersion")
        admin = run("auth", "can-i", "*", "*", "--all-namespaces", check=False).stdout.strip()
        nodes = json.loads(run("get", "nodes", "-o", "json").stdout)["items"]
        scs = json.loads(run("get", "storageclass", "-o", "json").stdout)["items"]
    except (RuntimeError, ValueError, KeyError) as e:
        print(json.dumps({"ok": False, "error": f"cannot read the cluster: {e}"}, indent=2))
        return 2
    result = evaluate(version, admin, nodes, scs, args.context, args.kubeconfig)
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())

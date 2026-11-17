#!/usr/bin/env python3
"""Read-only search for a ZooKeeper already running in the cluster.

    find_zookeeper.py --context CTX [--kubeconfig PATH]

Prints JSON: candidate `host:port` servers (Services exposing 2181, best first) and the ZooKeeper
StatefulSets found. Whether a candidate answers is checked separately with a `srvr` probe pod
(references/lock-provider.md).
"""
import argparse
import json
import subprocess
import sys

ZK_PORT = 2181
ZK_HINTS = ("zookeeper", "zk")


def looks_like_zk(name, images=()):
    name = name.lower()
    return any(h in name for h in ZK_HINTS) or any("zookeeper" in i.lower() for i in images)


def statefulsets(items):
    found = []
    for s in items:
        md, spec = s["metadata"], s.get("spec") or {}
        images = [c.get("image", "") for c in ((spec.get("template") or {}).get("spec") or {}).get("containers") or []]
        if looks_like_zk(md["name"], images):
            found.append({"namespace": md["namespace"], "name": md["name"], "images": images,
                          "replicas": spec.get("replicas", 1),
                          "ready_replicas": (s.get("status") or {}).get("readyReplicas", 0)})
    return found


def candidates(services, zk_sets):
    zk_namespaces = {z["namespace"] for z in zk_sets}
    found = []
    for s in services:
        md, spec = s["metadata"], s.get("spec") or {}
        if not any(p.get("port") == ZK_PORT for p in spec.get("ports") or []):
            continue
        found.append({
            "server": f"{md['name']}.{md['namespace']}.svc.cluster.local:{ZK_PORT}",
            "namespace": md["namespace"],
            "service": md["name"],
            "headless": spec.get("clusterIP") == "None",
            "next_to_statefulset": md["namespace"] in zk_namespaces,
        })
    # Prefer a ClusterIP Service in a namespace that also holds a ZooKeeper StatefulSet.
    found.sort(key=lambda c: (not c["next_to_statefulset"], c["headless"], not looks_like_zk(c["service"]), c["server"]))
    return found


def find(sts_items, svc_items):
    zk_sets = statefulsets(sts_items)
    return {"candidates": candidates(svc_items, zk_sets), "statefulsets": zk_sets}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--context", required=True, help="the context the user named; never the current one")
    parser.add_argument("--kubeconfig")
    args = parser.parse_args(argv)
    base = ["kubectl", "--request-timeout=20s"]
    if args.kubeconfig:
        base += ["--kubeconfig", args.kubeconfig]
    base += ["--context", args.context]
    out = {}
    for kind in ("statefulsets", "services"):
        p = subprocess.run(base + ["get", kind, "-A", "-o", "json"], capture_output=True, text=True)
        if p.returncode != 0:
            print(json.dumps({"error": f"kubectl get {kind} failed: {p.stderr.strip()[-300:]}"}))
            return 2
        out[kind] = json.loads(p.stdout)["items"]
    print(json.dumps(find(out["statefulsets"], out["services"]), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

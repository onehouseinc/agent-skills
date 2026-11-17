#!/usr/bin/env python3
"""The plan-and-checklist file /onehouse-autosetup keeps up to date; see references/progress-file.md."""
import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DIR = Path.home() / "onehouse-autosetup"

STEPS = (
    ("cli", "Onehouse CLI and project", (
        ("version", "`oh` CLI 0.7.0 or later"),
        ("tools", "`kubectl`, `helm` 3.8+ and `python3` installed"),
        ("login", "Signed in to Onehouse"),
        ("project", "Project is Self-Managed Kubernetes (GENERIC_K8S, BYOK, Enterprise)"),
    )),
    ("kube", "Kubernetes access", (
        ("context", "Cluster context chosen and confirmed"),
        ("admin", "Context has cluster-admin"),
        ("storageclass", "Exactly one default StorageClass"),
    )),
    ("storage", "Object storage", (
        ("endpoint", "Endpoint reachable"),
        ("tls", "Certificate publicly trusted"),
        ("credentials", "Access key accepted"),
        ("bucket", "Customer bucket found or created"),
        ("permissions", "Read, write, list and delete allowed"),
        ("register", "Registered with Onehouse"),
    )),
    ("nodes", "Node capacity and labels", (
        ("capacity", "Enough essential, worker and repair capacity"),
        ("roles", "Role labels on every node"),
        ("types", "`oh-instance-type` label on every node"),
    )),
    ("lock", "Lock provider", (
        ("chosen", "ZooKeeper chosen: existing, or a new one installed"),
        ("reachable", "Answers from inside the cluster"),
        ("registered", "Registered with Onehouse, with the StorageClass"),
    )),
    ("dpconnect", "dp-connect", (
        ("values", "Connection settings generated (never written to disk)"),
        ("chart", "Helm chart installed"),
        ("pods", "Images pulled; dp-connect pods running"),
        ("onehouse", "Reached Onehouse"),
        ("egress", "Outbound access to every required host"),
        ("preflight", "Cluster checks passed: storage, lock provider, labels"),
        ("nodetypes", "Node types reported to Onehouse"),
    )),
    ("create", "Create the data plane", (
        ("started", "Provisioning started"),
        ("components", "Onehouse components installed (about an hour)"),
        ("finished", "Workflow completed"),
    )),
    ("summary", "Summary", (
        ("cleanup", "No leftover values directory"),
        ("report", "Reused and created resources reported"),
    )),
)
STEP_IDS = tuple(step[0] for step in STEPS)
ITEM_IDS = {step_id: tuple(item_id for item_id, _ in items) for step_id, _, items in STEPS}
STATUS_LABELS = {
    "pending": "not started",
    "in_progress": "in progress",
    "done": "done",
    "skipped": "already in place",
    "blocked": "blocked",
}
CHECKED = {"done", "skipped"}
OPEN = {"pending", "in_progress"}
FACTS = (
    ("kube_context", "Kube context"),
    ("link_uid", "Link UID"),
    ("bucket", "Bucket"),
    ("storage_endpoint", "Storage endpoint"),
    ("zookeeper_servers", "Lock provider (ZooKeeper)"),
    ("chart_version", "dp-connect chart"),
    ("values_dir", "Leftover values directory (older runs)"),
    ("workflow_id", "Provisioning workflow"),
)
FACT_KEYS = tuple(key for key, _ in FACTS)
PROJECT_ID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
MAX_TEXT = 300

# Kept under 56 columns so terminal Markdown viewers (Warp's included) don't wrap it.
PLAN = """\
 YOU PROVIDE
   • Onehouse project ID
   • kubectl context with cluster-admin
   • S3 endpoint + access key (in a browser form)
                       │
                       ▼
 /onehouse-autosetup, asking before every change
 ┌──────────────────────────────────────────────┐
 │ 1  CLI and project     oh, kubectl, helm     │
 │ 2  Kubernetes access   admin, StorageClass   │
 │ 3  Object storage      key check, bucket     │
 │ 4  Nodes               capacity, labels      │
 │ 5  Lock provider       use or add ZooKeeper  │
 │ 6  dp-connect          chart and checks      │
 │ 7  Create data plane   about an hour         │
 │ 8  Summary             reused vs. created    │
 └──────────────────────────────────────────────┘
                       │
                       ▼
               Data plane ready"""


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def clean(text):
    return " ".join(str(text).split())[:MAX_TEXT].replace("|", "/")


def paths(base, project):
    folder = Path(base).expanduser() / project.lower()
    return folder / "setup-plan.md", folder / ".state.json"


def new_step():
    return {"status": "pending", "note": "", "items": {}}


def new_state(project, stamp):
    return {
        "project": project.lower(),
        "started": stamp,
        "updated": stamp,
        "steps": {step_id: new_step() for step_id in STEP_IDS},
        "facts": {},
        "log": [],
    }


def suffix(status, note):
    text = f" — {STATUS_LABELS[status]}" if status not in ("pending", "done") else ""
    if status == "blocked":
        text = " — **blocked**"
    return text + (f" · _{note}_" if note else "")


def checklist(state):
    complete = sum(state["steps"][step_id]["status"] in CHECKED for step_id in STEP_IDS)
    lines = [f"**{complete} of {len(STEPS)} steps complete**", ""]
    for number, (step_id, title, items) in enumerate(STEPS, start=1):
        step = state["steps"][step_id]
        mark = "x" if step["status"] in CHECKED else " "
        lines.append(f"- [{mark}] **{number}. {title}**{suffix(step['status'], step['note'])}")
        for item_id, label in items:
            item = step["items"].get(item_id, {"status": "pending", "note": ""})
            mark = "x" if item["status"] in CHECKED else " "
            lines.append(f"  - [{mark}] {label}{suffix(item['status'], item['note'])}")
    return lines


def render(state):
    rows = [("Project ID", f"`{state['project']}`"), ("Started", state["started"]),
            ("Last updated", state["updated"])]
    rows += [(label, f"`{state['facts'][key]}`") for key, label in FACTS if state["facts"].get(key)]
    lines = [
        "# Onehouse data plane setup",
        "",
        *(f"- **{label}:** {value}" for label, value in rows),
        "",
        "> `/onehouse-autosetup` updates this file after each step. It never contains secrets.",
        "> Run the skill again at any time to pick up where it stopped.",
        "",
        "## Plan",
        "",
        "```text",
        PLAN,
        "```",
        "",
        "## Checklist",
        "",
        *checklist(state),
        "",
        "## Log",
        "",
        "| Time | Step | Status | Note |",
        "|---|---|---|---|",
    ]
    for entry in state["log"]:
        where = entry["step"] + (f" / {entry['item']}" if entry.get("item") else "")
        lines.append(f"| {entry['time']} | {where} | {STATUS_LABELS[entry['status']]} | {entry['note']} |")
    return "\n".join(lines) + "\n"


def summary(md_path, state, created):
    steps = [{"id": step_id, "title": title, **state["steps"][step_id]} for step_id, title, _ in STEPS]
    pending = [s["id"] for s in steps if s["status"] not in CHECKED]
    return {"path": str(md_path), "created": created, "next_step": pending[0] if pending else None,
            "steps": steps, "facts": state["facts"]}


def write(md_path, state_path, state):
    md_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    md_path.parent.chmod(0o700)
    for path, text in ((state_path, json.dumps(state, indent=2)), (md_path, render(state))):
        path.touch(mode=0o600)
        path.chmod(0o600)
        path.write_text(text, encoding="utf-8")


def load(state_path):
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if "zookeeper" in state["steps"] and "lock" not in state["steps"]:
        old = state["steps"].pop("zookeeper")
        old["items"] = {("chosen" if k == "found" else k): v for k, v in old.get("items", {}).items()}
        state["steps"]["lock"] = old
    for step_id in STEP_IDS:
        state["steps"].setdefault(step_id, new_step()).setdefault("items", {})
    return state


def parse_facts(pairs):
    facts = {}
    for pair in pairs or ():
        key, sep, value = pair.partition("=")
        if not sep or key not in FACT_KEYS:
            raise ValueError(f"--set takes KEY=VALUE with KEY one of: {', '.join(FACT_KEYS)}")
        facts[key] = clean(value)
    return facts


def apply_update(state, step_id, item_id, status, note):
    step = state["steps"][step_id]
    if item_id:
        step["items"][item_id] = {"status": status, "note": note}
        if step["status"] == "pending":
            step["status"] = "in_progress"
        return
    step["status"], step["note"] = status, note
    if status in CHECKED:
        for open_id in ITEM_IDS[step_id]:
            if step["items"].get(open_id, {"status": "pending"})["status"] in OPEN:
                step["items"][open_id] = {"status": status, "note": ""}


def main(argv=None, stamp=None):
    parser = argparse.ArgumentParser(description="Maintain the /onehouse-autosetup plan-and-checklist file.")
    parser.add_argument("command", choices=("plan", "init", "update", "show", "checklist"))
    parser.add_argument("--project", help="the Onehouse project ID")
    parser.add_argument("--dir", default=str(DEFAULT_DIR), help="where to keep the file (default: %(default)s)")
    parser.add_argument("--step", choices=STEP_IDS)
    parser.add_argument("--item", help="one check within --step; see references/progress-file.md")
    parser.add_argument("--status", choices=tuple(STATUS_LABELS))
    parser.add_argument("--note", default="")
    parser.add_argument("--set", action="append", metavar="KEY=VALUE", dest="facts")
    args = parser.parse_args(argv)
    stamp = stamp or now()

    if args.command == "plan":
        print(PLAN)
        return 0
    if not args.project or not PROJECT_ID.match(args.project):
        parser.error("--project must be the project ID, a UUID such as 12345678-90ab-cdef-1234-567890abcdef")
    md_path, state_path = paths(args.dir, args.project)

    if args.command == "init":
        created = not state_path.exists()
        state = new_state(args.project, stamp) if created else load(state_path)
        write(md_path, state_path, state)
    else:
        if not state_path.exists():
            parser.error(f"no progress file for this project yet; run init first ({md_path})")
        state, created = load(state_path), False
        if args.command == "checklist":
            print("\n".join(checklist(state)))
            return 0
        if args.command == "update":
            if not args.step or not args.status:
                parser.error("update needs --step and --status")
            if args.item and args.item not in ITEM_IDS[args.step]:
                parser.error(f"--item for {args.step} is one of: {', '.join(ITEM_IDS[args.step])}")
            try:
                state["facts"].update(parse_facts(args.facts))
            except ValueError as exc:
                parser.error(str(exc))
            note = clean(args.note)
            apply_update(state, args.step, args.item, args.status, note)
            state["updated"] = stamp
            state["log"].append({"time": stamp, "step": args.step, "item": args.item or "",
                                 "status": args.status, "note": note})
            write(md_path, state_path, state)

    json.dump(summary(md_path, state, created), sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())

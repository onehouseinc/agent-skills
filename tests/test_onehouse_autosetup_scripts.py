"""Unit tests for the onehouse-autosetup skill's scripts (no cluster needed).

Run: pip install pytest pyyaml && pytest tests
"""

import importlib.util
import json
import re
import subprocess
from pathlib import Path

import pytest
import yaml

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "onehouse-autosetup" / "scripts"


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cc = load("check_cluster")
fz = load("find_zookeeper")
pg = load("progress")

ESS = {"oh-essential-pods": "true", "oh-essential-pods-vpc-cni": "true", "monitoring-pods": "true"}
WRK = {"driver-pods": "true", "job-workers": "true", "sql-workers": "true"}
REP = {"pool": "job-workers-repair"}
DEFAULT_SC = [{"metadata": {"name": "standard", "annotations": {"storageclass.kubernetes.io/is-default-class": "true"}}}]


def node(name, cpu=4, cap="16393692Ki", alloc="13631488Ki", alloc_cpu="3900m", labels=None, ready=True, taint=False):
    raw = {
        "metadata": {"name": name, "labels": dict(labels or {})},
        "spec": {},
        "status": {
            "capacity": {"cpu": str(cpu), "memory": cap},
            "allocatable": {"cpu": alloc_cpu, "memory": alloc},
            "conditions": [{"type": "Ready", "status": "True" if ready else "False"}],
        },
    }
    if taint:
        raw["spec"]["taints"] = [{"key": "dedicated", "effect": "NoSchedule"}]
    return raw


def small(name, labels=None):
    return node(name, cap="8148368Ki", alloc="6291456Ki", labels=labels)


def run(nodes, scs=DEFAULT_SC, admin="yes"):
    return cc.evaluate("v1.33.1", admin, nodes, scs, context="ctx")


def check(result, name):
    return next(c for c in result["checks"] if c["name"] == name)


class TestQuantities:
    @pytest.mark.parametrize("q,want", [("16Gi", 16), ("16384Mi", 16), ("16777216Ki", 16), (str(2**30), 1)])
    def test_gib(self, q, want):
        assert cc.gib(q) == pytest.approx(want)

    @pytest.mark.parametrize("q,want", [("3900m", 3.9), ("4", 4.0)])
    def test_cpu(self, q, want):
        assert cc.cpu(q) == want


class TestFreshCluster:
    def test_minimum_cluster_passes_and_proposes_every_role(self):
        nodes = [small(f"e{i}") for i in range(4)] + [node("w1", cpu=8, alloc_cpu="7900m"), node("r1")]
        r = run(nodes)
        assert r["ok"], r["checks"]
        a = r["label_plan"]["assignments"]
        assert a["r1"] == ["repair"]
        assert [a[f"e{i}"] for i in range(4)] == [["essential"]] * 4
        assert a["w1"] == ["workers"]
        assert r["add_nodes"] == []

    def test_repair_node_gets_pool_label_and_no_job_workers(self):
        r = run([small(f"e{i}") for i in range(4)] + [node("w1"), node("r1")])
        change = next(c for c in r["label_plan"]["changes"] if c["roles"] == ["repair"])
        assert change["set"]["pool"] == "job-workers-repair"
        assert "job-workers" not in change["set"]

    def test_one_instance_type_per_shape(self):
        r = run([small(f"e{i}") for i in range(4)] + [node("w1", cpu=8, alloc_cpu="7900m"), node("r1")])
        types = {t["oh_instance_type"]: t for t in r["label_plan"]["node_types"]}
        assert set(types) == {"oh-4cpu-8gi", "oh-8cpu-16gi", "oh-4cpu-16gi"}
        assert len(types["oh-4cpu-8gi"]["nodes"]) == 4
        for c in r["label_plan"]["changes"]:
            assert c["set"]["oh-instance-type"] == c["instance_type"]

    def test_memory_within_two_percent_is_one_shape(self):
        nodes = [node("a", cap="16393692Ki"), node("b", cap="16200000Ki")]
        assert len(cc.group_shapes([cc.node_info(n) for n in nodes])) == 1

    def test_provider_instance_type_is_reused_when_valid(self):
        label = {"node.kubernetes.io/instance-type": "S-4vCPU-16GB"}
        r = run([node(f"n{i}", labels=label) for i in range(4)])
        assert [t["oh_instance_type"] for t in r["label_plan"]["node_types"]] == ["s-4vcpu-16gb"]

    def test_commands_use_context_and_never_overwrite(self):
        r = run([small(f"e{i}") for i in range(4)] + [node("w1"), node("r1")])
        assert r["label_plan"]["commands"]
        for cmd in r["label_plan"]["commands"]:
            assert cmd.startswith("kubectl --context ctx label node ")
            assert "--overwrite" not in cmd
        assert r["label_plan"]["conflict_commands"] == []

    def test_untainted_extra_nodes_become_workers(self):
        r = run([small(f"e{i}") for i in range(4)] + [node("r1"), node("w1"), node("w2")])
        assert r["label_plan"]["assignments"]["w2"] == ["workers"]

    def test_tainted_and_not_ready_nodes_are_skipped(self):
        nodes = [small(f"e{i}") for i in range(4)] + [node("r1"), node("w1"),
                                                       node("t1", taint=True), node("nr", ready=False)]
        r = run(nodes)
        assert r["label_plan"]["assignments"]["t1"] == []
        assert "nr" not in r["label_plan"]["assignments"]


class TestInsufficientCapacity:
    def test_two_small_nodes_prints_what_to_add(self):
        r = run([small("a"), small("b")])
        assert not r["ok"]
        add = {a["role"]: a for a in r["add_nodes"]}
        assert set(add) == {"essential", "workers", "repair"}
        assert add["repair"]["add_nodes"] == 1 and add["repair"]["min_allocatable_gib"] == 12
        assert add["essential"]["add_nodes"] >= 1
        assert add["essential"]["labels"] == ESS

    def test_three_16g_nodes_short_one_worker(self):
        r = run([node("a"), node("b"), node("c")])
        add = {a["role"]: a for a in r["add_nodes"]}
        assert set(add) == {"workers"}
        assert not check(r, "capacity_workers")["ok"]
        assert check(r, "capacity_essential")["ok"]

    def test_essential_short_on_memory(self):
        r = run([small("e1", ESS), small("e2", ESS), node("w1", labels=WRK), node("r1", labels=REP)])
        add = {a["role"]: a for a in r["add_nodes"]}
        assert add["essential"]["add_nodes"] == 1
        assert add["essential"]["missing_allocatable_gib"] == pytest.approx(8.0)

    def test_small_cpu_nodes_are_not_counted(self):
        tiny = [node(f"t{i}", cpu=2, alloc_cpu="1900m") for i in range(6)]
        r = run(tiny)
        assert not r["ok"]
        assert any("fewer than 4 vCPU" in w for w in r["warnings"])


class TestExistingLabels:
    def labelled(self):
        return [small(f"e{i}", {**ESS, "oh-instance-type": "ess"}) for i in range(4)] + [
            node("w1", labels={**WRK, "oh-instance-type": "wrk"}, cpu=8, alloc_cpu="7900m"),
            node("r1", labels={**REP, "oh-instance-type": "rep"}),
        ]

    def test_fully_labelled_cluster_needs_no_changes(self):
        r = run(self.labelled())
        assert r["ok"]
        assert r["label_plan"]["complete"]
        assert r["label_plan"]["commands"] == []

    def test_missing_instance_type_is_added_with_the_shape_value(self):
        nodes = self.labelled()
        del nodes[3]["metadata"]["labels"]["oh-instance-type"]
        r = run(nodes)
        (change,) = r["label_plan"]["changes"]
        assert change == {"node": "e3", "roles": ["essential"], "instance_type": "ess",
                          "set": {"oh-instance-type": "ess"}, "conflicts": []}

    def test_repair_prefers_a_node_without_a_pool_label(self):
        nodes = [small(f"e{i}") for i in range(4)] + [node("w1"), node("r1", labels={"pool": "batch"})]
        a = run(nodes)["label_plan"]["assignments"]
        assert a["w1"] == ["repair"] and a["r1"] == ["workers"]

    def test_conflicting_pool_label_is_not_overwritten(self):
        nodes = [small(f"e{i}") for i in range(4)] + [node("w1", cpu=8, alloc="30000000Ki"),
                                                       node("r1", labels={"pool": "batch"})]
        nodes[-2]["metadata"]["labels"] = dict(WRK)
        plan = run(nodes)["label_plan"]
        change = next(c for c in plan["changes"] if c["node"] == "r1")
        assert change["conflicts"] == [{"label": "pool", "current": "batch", "proposed": "job-workers-repair"}]
        assert "pool" not in change["set"]
        assert not any("pool=" in c for c in plan["commands"])
        assert any("--overwrite pool=job-workers-repair" in c for c in plan["conflict_commands"])

    def test_role_value_other_than_true_is_a_conflict(self):
        nodes = self.labelled()
        nodes[0]["metadata"]["labels"]["monitoring-pods"] = "false"
        r = run(nodes)
        (change,) = r["label_plan"]["changes"]
        assert change["set"] == {}
        assert change["conflicts"] == [{"label": "monitoring-pods", "current": "false", "proposed": "true"}]
        assert r["label_plan"]["conflict_commands"] == ["kubectl --context ctx label node e0 --overwrite monitoring-pods=true"]
        assert r["label_plan"]["commands"] == []

    def test_repair_node_with_job_workers_is_flagged(self):
        nodes = self.labelled()
        nodes[-1]["metadata"]["labels"]["job-workers"] = "true"
        r = run(nodes)
        assert any("several roles" in w for w in r["warnings"])

    def test_instance_type_spanning_two_shapes_is_a_conflict(self):
        nodes = self.labelled()
        nodes[0] = node("e0", cpu=8, alloc_cpu="7900m", labels={**ESS, "oh-instance-type": "ess"})
        r = run(nodes)
        change = next(c for c in r["label_plan"]["changes"] if c["node"] == "e0")
        assert change["conflicts"][0]["label"] == "oh-instance-type"
        assert change["conflicts"][0]["current"] == "ess"
        assert change["conflicts"][0]["proposed"] != "ess"

    def test_pre_labelled_cluster_does_not_spread_to_spare_nodes(self):
        r = run(self.labelled() + [node("spare")])
        assert r["label_plan"]["assignments"]["spare"] == []

    def test_openengine_node_needs_an_instance_type(self):
        r = run(self.labelled() + [node("oe", labels={"openengine-trino": "true"})])
        change = next(c for c in r["label_plan"]["changes"] if c["node"] == "oe")
        assert "oh-instance-type" in change["set"]


class TestClusterChecks:
    def test_not_cluster_admin_fails(self):
        r = run([node("a")], admin="no")
        assert not check(r, "cluster_admin")["ok"] and not r["ok"]

    @pytest.mark.parametrize("scs", [[], DEFAULT_SC * 2])
    def test_needs_exactly_one_default_storage_class(self, scs):
        r = run([small(f"e{i}") for i in range(4)] + [node("w1"), node("r1")], scs=scs)
        assert not check(r, "default_storage_class")["ok"]
        assert r["default_storage_class"] is None

    def test_main_uses_only_the_named_context_and_kubeconfig(self, monkeypatch, capsys):
        nodes = [small(f"e{i}") for i in range(4)] + [node("w1"), node("r1")]
        replies = {
            "version": json.dumps({"serverVersion": {"gitVersion": "v1.33.1"}}),
            "can-i": "yes",
            "nodes": json.dumps({"items": nodes}),
            "storageclass": json.dumps({"items": DEFAULT_SC}),
        }
        calls = []

        def fake(args, kubeconfig=None, context=None):
            calls.append((tuple(args), kubeconfig, context))
            key = next(k for k in replies if k in args)
            return subprocess.CompletedProcess(args, 0, stdout=replies[key], stderr="")

        monkeypatch.setattr(cc, "kubectl", fake)
        assert cc.main(["--context", "my-ctx", "--kubeconfig", "/k/do.yaml"]) == 0
        out = json.loads(capsys.readouterr().out)
        assert out["context"] == "my-ctx" and out["kubeconfig"] == "/k/do.yaml"
        assert all(c[1:] == ("/k/do.yaml", "my-ctx") for c in calls)
        assert not any("current-context" in c[0] for c in calls)
        assert out["label_plan"]["commands"][0].startswith("kubectl --kubeconfig /k/do.yaml --context my-ctx label node ")

    @pytest.mark.parametrize("module", ["cc", "fz"])
    def test_scripts_refuse_to_run_without_a_named_context(self, module, monkeypatch):
        script = {"cc": cc, "fz": fz}[module]
        monkeypatch.setattr(script, "subprocess", None, raising=False)
        with pytest.raises(SystemExit) as exc:
            script.main([])
        assert exc.value.code == 2

    def test_main_reports_unreachable_cluster(self, monkeypatch, capsys):
        monkeypatch.setattr(cc, "kubectl", lambda args, kubeconfig=None, context=None:
                            subprocess.CompletedProcess(args, 1, stdout="", stderr="connection refused"))
        assert cc.main(["--context", "x"]) == 2
        assert "connection refused" in json.loads(capsys.readouterr().out)["error"]


class TestFindZookeeper:
    def svc(self, ns, name, port=2181, headless=False):
        return {"metadata": {"namespace": ns, "name": name},
                "spec": {"ports": [{"port": port}], "clusterIP": "None" if headless else "10.0.0.1"}}

    def sts(self, ns, name, image="zookeeper:3.9"):
        return {"metadata": {"namespace": ns, "name": name},
                "spec": {"replicas": 1, "template": {"spec": {"containers": [{"image": image}]}}},
                "status": {"readyReplicas": 1}}

    def test_prefers_clusterip_next_to_statefulset(self):
        out = fz.find([self.sts("zk", "zk")],
                      [self.svc("other", "coord"), self.svc("zk", "zk-headless", headless=True), self.svc("zk", "zk")])
        assert out["candidates"][0]["server"] == "zk.zk.svc.cluster.local:2181"
        assert len(out["candidates"]) == 3

    def test_ignores_services_without_2181(self):
        out = fz.find([], [self.svc("a", "web", port=80)])
        assert out == {"candidates": [], "statefulsets": []}

    def test_detects_statefulset_by_image(self):
        out = fz.find([self.sts("infra", "coordination", image="bitnami/zookeeper:3.8")], [])
        assert out["statefulsets"][0]["name"] == "coordination"


class TestZookeeperManifest:
    def test_uses_default_storage_class_and_essential_nodes(self):
        docs = list(yaml.safe_load_all((SCRIPTS / "zookeeper.yaml").read_text()))
        sts = next(d for d in docs if d["kind"] == "StatefulSet")
        assert sts["spec"]["template"]["spec"]["nodeSelector"] == {"oh-essential-pods": "true"}
        for claim in sts["spec"]["volumeClaimTemplates"]:
            assert "storageClassName" not in claim["spec"]
        svc = next(d for d in docs if d["kind"] == "Service")
        assert svc["spec"]["ports"][0]["port"] == 2181


PROJECT = "12345678-90ab-cdef-1234-567890abcdef"


class TestProgressFile:
    def call(self, tmp_path, capsys, *args, stamp="2026-10-29 06:00 UTC"):
        assert pg.main([*args, "--project", PROJECT, "--dir", str(tmp_path)], stamp=stamp) == 0
        return json.loads(capsys.readouterr().out)

    def test_plan_prints_the_diagram_without_a_project(self, capsys):
        assert pg.main(["plan"]) == 0
        out = capsys.readouterr().out
        assert "1  CLI and project" in out and "8  Summary" in out and "Onehouse project ID" in out
        assert max(len(line) for line in out.splitlines()) <= 56
        box = [line for line in out.splitlines() if line.startswith((" ┌", " │ ", " └"))]
        assert len({len(line) for line in box}) == 1
        assert "5  Lock provider" in out

    def test_init_creates_the_file_with_every_step_unchecked(self, tmp_path, capsys):
        out = self.call(tmp_path, capsys, "init")
        md = Path(out["path"])
        assert md == tmp_path / PROJECT / "setup-plan.md" and out["created"]
        text = md.read_text()
        assert text.count("\n- [ ] **") == 8 and "## Plan" in text and pg.PLAN in text
        assert text.count("\n  - [ ] ") == sum(len(items) for _, _, items in pg.STEPS)
        assert "<br>" not in text and "| |" not in text
        assert out["next_step"] == "cli"

    def test_update_ticks_the_step_logs_it_and_records_facts(self, tmp_path, capsys):
        self.call(tmp_path, capsys, "init")
        out = self.call(tmp_path, capsys, "update", "--step", "cli", "--status", "done",
                        "--note", "signed in | GENERIC_K8S", "--set", "kube_context=do-nyc3",
                        stamp="2026-10-29 06:05 UTC")
        text = Path(out["path"]).read_text()
        assert "- [x] **1. Onehouse CLI and project** · _signed in / GENERIC_K8S_" in text
        assert "  - [x] Signed in to Onehouse\n" in text
        assert "| 2026-10-29 06:05 UTC | cli | done | signed in / GENERIC_K8S |" in text
        assert "- **Kube context:** `do-nyc3`" in text
        assert "**1 of 8 steps complete**" in text
        assert out["next_step"] == "kube"

    def test_item_update_ticks_one_check_and_starts_the_step(self, tmp_path, capsys):
        self.call(tmp_path, capsys, "init")
        self.call(tmp_path, capsys, "update", "--step", "dpconnect", "--item", "chart", "--status", "done")
        out = self.call(tmp_path, capsys, "update", "--step", "dpconnect", "--item", "egress",
                        "--status", "blocked", "--note", "registry-1.docker.io:443 unreachable")
        text = Path(out["path"]).read_text()
        assert "- [ ] **6. dp-connect** — in progress" in text
        assert "  - [x] Helm chart installed\n" in text
        assert "  - [ ] Outbound access to every required host — **blocked** · _registry-1.docker.io:443 unreachable_" in text
        assert "| dpconnect / egress | blocked |" in text

    def test_finishing_a_step_fills_its_open_checks_but_keeps_recorded_ones(self, tmp_path, capsys):
        self.call(tmp_path, capsys, "init")
        self.call(tmp_path, capsys, "update", "--step", "nodes", "--item", "types", "--status", "done")
        out = self.call(tmp_path, capsys, "update", "--step", "nodes", "--status", "skipped")
        items = next(s for s in out["steps"] if s["id"] == "nodes")["items"]
        assert items["types"]["status"] == "done"
        assert items["capacity"]["status"] == items["roles"]["status"] == "skipped"

    def test_checklist_prints_markdown_for_the_chat(self, tmp_path, capsys):
        self.call(tmp_path, capsys, "init")
        assert pg.main(["checklist", "--project", PROJECT, "--dir", str(tmp_path)]) == 0
        out = capsys.readouterr().out
        assert out.startswith("**0 of 8 steps complete**") and "  - [ ] Node types reported to Onehouse" in out

    def test_state_from_an_older_cli_still_loads(self, tmp_path, capsys):
        self.call(tmp_path, capsys, "init")
        state_path = tmp_path / PROJECT / ".state.json"
        state = json.loads(state_path.read_text())
        for step in state["steps"].values():
            del step["items"]
        state_path.write_text(json.dumps(state))
        out = self.call(tmp_path, capsys, "update", "--step", "cli", "--item", "version", "--status", "done")
        assert "  - [x] `oh` CLI 0.7.0 or later" in Path(out["path"]).read_text()

    def test_zookeeper_step_from_an_older_version_becomes_lock(self, tmp_path, capsys):
        self.call(tmp_path, capsys, "init")
        state_path = tmp_path / PROJECT / ".state.json"
        state = json.loads(state_path.read_text())
        del state["steps"]["lock"]
        state["steps"]["zookeeper"] = {"status": "done", "note": "",
                                       "items": {"found": {"status": "done", "note": ""}}}
        state_path.write_text(json.dumps(state))
        out = self.call(tmp_path, capsys, "show")
        lock = next(s for s in out["steps"] if s["id"] == "lock")
        assert lock["status"] == "done" and lock["items"]["chosen"]["status"] == "done"

    def test_unknown_item_is_refused(self, tmp_path, capsys):
        self.call(tmp_path, capsys, "init")
        with pytest.raises(SystemExit):
            pg.main(["update", "--project", PROJECT, "--dir", str(tmp_path), "--step", "cli",
                     "--item", "egress", "--status", "done"])

    @pytest.mark.parametrize("status, checked", [("skipped", True), ("blocked", False), ("in_progress", False)])
    def test_only_done_and_skipped_tick_the_box(self, tmp_path, capsys, status, checked):
        self.call(tmp_path, capsys, "init")
        out = self.call(tmp_path, capsys, "update", "--step", "nodes", "--status", status)
        line = next(l for l in Path(out["path"]).read_text().splitlines() if "4. Node capacity" in l)
        assert line.startswith("- [x]" if checked else "- [ ]")

    def test_rerun_keeps_existing_progress(self, tmp_path, capsys):
        self.call(tmp_path, capsys, "init")
        self.call(tmp_path, capsys, "update", "--step", "cli", "--status", "done")
        out = self.call(tmp_path, capsys, "init")
        assert not out["created"] and out["next_step"] == "kube"

    def test_unknown_fact_keys_are_refused(self, tmp_path, capsys):
        self.call(tmp_path, capsys, "init")
        with pytest.raises(SystemExit):
            pg.main(["update", "--project", PROJECT, "--dir", str(tmp_path), "--step", "storage",
                     "--status", "done", "--set", "secret_access_key=s3cr3t-value"])
        assert "s3cr3t-value" not in (tmp_path / PROJECT / "setup-plan.md").read_text()

    def test_files_are_private_to_the_user(self, tmp_path, capsys):
        folder = tmp_path / PROJECT
        folder.mkdir(mode=0o755)
        (folder / "setup-plan.md").write_text("")
        (folder / "setup-plan.md").chmod(0o644)
        self.call(tmp_path, capsys, "init")
        assert folder.stat().st_mode & 0o777 == 0o700
        for name in ("setup-plan.md", ".state.json"):
            assert (folder / name).stat().st_mode & 0o777 == 0o600

    def test_values_dir_is_recorded_then_cleared(self, tmp_path, capsys):
        self.call(tmp_path, capsys, "init")
        out = self.call(tmp_path, capsys, "update", "--step", "dpconnect", "--status", "in_progress",
                        "--set", "values_dir=/tmp/tmp.abc123")
        assert out["facts"]["values_dir"] == "/tmp/tmp.abc123"
        out = self.call(tmp_path, capsys, "update", "--step", "dpconnect", "--status", "in_progress",
                        "--set", "values_dir=")
        assert not out["facts"]["values_dir"]
        assert "tmp.abc123" not in Path(out["path"]).read_text().split("## Log")[0]

    @pytest.mark.parametrize("args", [["init"], ["init", "--project", "../etc"], ["update", "--project", PROJECT]])
    def test_bad_input_is_an_error(self, tmp_path, args):
        with pytest.raises(SystemExit):
            pg.main([*args, "--dir", str(tmp_path)])


REPO_ROOT = SCRIPTS.parents[2]
# Copied from PyPI's record for this wheel; bump both together.
EXPECTED_CLI_VERSION = "0.7.0"
EXPECTED_CLI_SHA256 = "2067a6f9b6d671877ff35a20631e027aeab604a2dcad3ef11af0ae3e05814746"
PIP_LINE = re.compile(r"pip install[^\n`]*onehouse-cli[^\n`]*")
PINNED = re.compile(
    r"'onehouse-cli\[dataplane\] @ https://files\.pythonhosted\.org/packages/[0-9a-f/]+/"
    r"onehouse_cli-(\d+\.\d+\.\d+)-py3-none-any\.whl#sha256=([0-9a-f]{64})'"
)


def test_cli_install_is_pinned_by_hash():
    docs = [p for p in REPO_ROOT.rglob("*.md") if ".git" not in p.parts]
    lines = [m.group(0) for p in docs for m in PIP_LINE.finditer(p.read_text())]
    assert lines, "the skill should tell the agent how to install the CLI"
    for line in lines:
        pinned = PINNED.search(line)
        assert pinned, f"unpinned CLI install: {line}"
        assert pinned.groups() == (EXPECTED_CLI_VERSION, EXPECTED_CLI_SHA256)
        # `dataplane install` and `dataplane preflight` first ship in 0.7.0.
        assert tuple(map(int, pinned.group(1).split("."))) >= (0, 7, 0)


def test_skill_never_writes_values_or_reads_raw_job_logs():
    docs = "\n".join(p.read_text() for p in (REPO_ROOT / "skills").rglob("*.md"))
    assert "dataplane install" in docs and "dataplane preflight" in docs
    assert not re.search(r"dataplane template[^\n`]*--out", docs)
    assert not re.search(r"logs job/", docs)

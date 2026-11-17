# The progress file (`scripts/progress.py`)

The skill keeps a Markdown file holding the plan diagram, a checklist of the eight steps, each with
its own checks, and a log. The user can keep it open in an editor or previewer and see where setup
stands without scrolling the chat. It lives at:

```
~/onehouse-autosetup/<project id>/setup-plan.md
```

The script renders it from `.state.json` in the same folder. Never edit either file by hand; always
go through the script, so the checklist, the log and the facts stay consistent. The file is plain
Markdown with no HTML, and the diagram is under 56 columns, so it reads the same in a terminal
viewer (Warp, `glow`), an editor preview or on GitHub.

| Command | What it does |
|---|---|
| `progress.py plan` | Prints the plan diagram, for showing in the chat. |
| `progress.py init --project <id>` | Creates the file, or reuses an existing one, and prints JSON: `path`, `created`, `next_step` and every step's status and checks. On a re-run, `created` is false: show the user the existing status, then carry on. |
| `progress.py update --project <id> --step <step> [--item <check>] --status <status> [--note "<text>"] [--set KEY=VALUE …]` | Records one step's or one check's outcome, appends it to the log and re-renders the file. Prints JSON. |
| `progress.py checklist --project <id>` | Prints the checklist as Markdown, for showing in the chat after a step. |
| `progress.py show --project <id>` | Prints the current state as JSON without changing it. |

## Steps and their checks

Tick each check with `--item` as soon as it's known, so the file shows progress inside a long step.

| `--step` | `--item` values, in order |
|---|---|
| `cli` | `version` (`oh` 0.7.0 or later), `tools` (kubectl, helm 3.8+, python3), `login`, `project` (Self-Managed Kubernetes: GENERIC_K8S, BYOK, Enterprise) |
| `kube` | `context` (chosen and confirmed), `admin` (cluster-admin), `storageclass` (one default) |
| `storage` | `endpoint`, `tls`, `credentials`, `bucket`, `permissions`, `register`: the same names as the `checks` that `oh dataplane storage connect` returns |
| `nodes` | `capacity`, `roles` (role labels), `types` (`oh-instance-type`) |
| `lock` | `chosen` (existing ZooKeeper picked, or one installed), `reachable` (answers from the cluster), `registered` (`oh dataplane set`) |
| `dpconnect` | `values`, `chart`, `pods`, `onehouse`, `egress`, `preflight`, `nodetypes`; see below |
| `create` | `started`, `components` (note the current task), `finished` |
| `summary` | `cleanup` (no values directory from an older run left), `report` |

### dp-connect checks

What each one means, and where its evidence comes from. `K` and `H` are as defined in SKILL.md.

| `--item` | Ticked when | Evidence |
|---|---|---|
| `values` | the connection settings are generated from the registered storage | `oh dataplane install` returns a `chart_version` |
| `chart` | the release is `deployed` | `H -n oh-dpconnect status byok-dp-connect` |
| `pods` | the images pulled and the dp-connect pods are `Running` or `Completed` | `K -n oh-dpconnect get pods`. `ImagePullBackOff` means the image registry is unreachable or rate-limited: mark `egress` blocked too |
| `onehouse` | dp-connect reached Onehouse | `preflight` shows a `state` other than `not_started`, and `K -n oh-connectivity get pods` shows every pod `Running` |
| `egress` | every required host is reachable from the cluster | `preflight` lists every `required` `egress` host with `tcp` true, and no `GENERIC_K8S_REQUIRED_EGRESS_UNREACHABLE` failure |
| `preflight` | the storage probe, the lock provider and the node labels pass | `preflight` shows `state` `green`, and `dp_connect_status` is `DP_CONNECT_STATUS_SUCCESSFUL` |
| `nodetypes` | one node type per `oh-instance-type`, with the roles from Step 4 | `oh -o json dataplane node-types --org <project id>` |

`preflight` is `oh -o json dataplane preflight --org <project id> --kube-context <ctx> [--kubeconfig
<kubeconfig>]`. It reads the control plane's verdict and the newest dp-connect Job, and returns only
pattern-checked fields, never raw log lines. Each entry in `failures` has a `code` from
[troubleshooting.md](troubleshooting.md#preflight-codes). Mark the matching check `blocked`, with the
host or reason in `--note`. dp-connect re-checks every 2 minutes, so tick it once a later pass is clean.

## Statuses

- `in_progress`: when a step starts. A check recorded on a `pending` step moves the step to
  `in_progress` on its own.
- `done`: the step or check changed something, or passed.
- `skipped`: it was already in place.
- `blocked`: waiting on the user. Put the reason in `--note`.

Marking a step `done` or `skipped` gives the same status to every check in it that's still open.
Checks already recorded keep their own status.

## Facts, notes and secrets

- **`--set`** records facts for the list at the top of the file. It accepts only `kube_context`,
  `link_uid`, `bucket`, `storage_endpoint`, `zookeeper_servers`, `chart_version`, `values_dir` and
  `workflow_id`. Any other key is refused.
- **Permissions:** the folder is `0700` and both files are `0600`, reset on every write.
- **Notes:** keep `--note` to one short, plain line, such as "2 essential nodes labelled" or
  "registry-1.docker.io:443 unreachable". Notes are capped at 300 characters.
- **Never record secrets.** That means no access key, no secret, no token and no values-file
  content in a note or a fact.
- **Another location:** `--dir <path>` puts the file elsewhere. Pass the same `--dir` on every call.

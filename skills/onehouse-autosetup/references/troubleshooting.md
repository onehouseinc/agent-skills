# Troubleshooting

`K` is `kubectl [--kubeconfig <kubeconfig>] --context <ctx>` and `H` is `helm [--kubeconfig <kubeconfig>] --kube-context <ctx>`, with the context the user named in Step 2. Status comes from
`oh -o json dataplane describe --org <project id>` (`dp_connect_status`, `workflow.status`,
`workflow.failed_task`, `workflow.error_message`). Every fix below is something the user can do in
their own cluster, storage or Onehouse console. When none applies, go to
[Escalate](#escalate).

## `oh` and access

| Symptom | Fix |
|---|---|
| `No such command 'dataplane'` or `'install'`, or `oh --version` older than 0.7.0 | the CLI is too old for this setup: install the pinned, hash-checked version from SKILL.md Step 1 |
| auth error, 401, "not signed in", expired session | the user runs `! oh login` (it opens a browser; never run it for them) |
| `PERMISSION_DENIED` / 403 on an `oh dataplane` command | the user isn't an admin of that project, or data-plane onboarding through the API isn't enabled for it. Check the project ID; ask the Onehouse contact to enable it |
| org is not GENERIC_K8S, BYOK or ENTERPRISE | can't be changed on an existing project. Create a new project in the Onehouse console with the Self-Managed Kubernetes cloud and use its ID |

## dp-connect (`dp_connect_status`)

`DP_CONNECT_STATUS_` + `PENDING` -> `STARTED` -> `SECRETS_READY` -> `SUCCESSFUL`. It reports `SUCCESSFUL` once the chart
is installed, connected to Onehouse and has sent the node inventory.

| Symptom | Fix |
|---|---|
| stays `PENDING` | the chart isn't installed or its Job hasn't started: `H -n <namespace> status <release>`, `K -n <namespace> get pods` |
| `ImagePullBackOff` | the image registry is unreachable from the nodes, or rate-limited: check node egress; wait and retry. `K -n <namespace> describe pod <pod>` shows which |
| the dp-connect Job failed | `oh -o json dataplane preflight --org <project id> --kube-context <ctx>` shows `job` `failed` and the failure codes. From chart 1.4.0, `oh dataplane install` again replaces the Job. Before 1.4.0 it doesn't rerun on upgrade: `H -n <namespace> uninstall <release> --wait`, then `oh dataplane install` [confirm] |
| dp-connect pods `Pending` | no node has `oh-essential-pods-vpc-cni=true`: rerun the node step |
| storage changed after the install (new endpoint or re-registered) | run `oh dataplane install` again [confirm]; it generates fresh settings from the registered storage |
| `preflight` says the control plane returned no verdict | the control plane predates the field, or dp-connect hasn't reported yet: pass `--kube-context` so it reads the Job instead |

## Preflight codes

The preflight runs in the first workflow task, `GENERIC_K8S_DP_CONFIG_CREATION`, on what dp-connect
reported. `workflow.error_message` carries one or more codes, joined with `; `. After a fix,
dp-connect re-reports within about 2 minutes; then run `oh dataplane create` again.

| Code | Meaning | Fix |
|---|---|---|
| `GENERIC_K8S_NODE_INVENTORY_MISSING` | dp-connect never reported nodes, or the installed chart is too old to | `H uninstall --wait`, then `oh dataplane install` (it installs the chart version Onehouse sets) |
| `GENERIC_K8S_REQUIRED_NODE_LABEL_MISSING` | no Ready node carries `oh-essential-pods=true`, `monitoring-pods=true` or `pool=job-workers-repair` together with an `oh-instance-type` | rerun `check_cluster.py` and apply its plan |
| `GENERIC_K8S_INSTANCE_TYPE_LABEL_MISSING` | some role-labelled nodes have no `oh-instance-type` | the message names how many: `check_cluster.py` names the nodes and the value |
| `GENERIC_K8S_STORAGE_CLASS_MISSING` | the registered StorageClass doesn't exist, or there's no default class | `K get storageclass`; `oh dataplane set --storage-class <class>` (only the storage class changes; the servers stay) |
| `GENERIC_K8S_STORAGE_ENDPOINT_UNREACHABLE` | pods can't resolve or reach the endpoint, or its certificate isn't publicly trusted | test from a pod [confirm]: `K run oh-s3-probe --rm -i --restart=Never --image=curlimages/curl:8.23.0@sha256:3fb7e13e5d179a845c97d78204f3f1064d1907037f26a3ebf91710571151fd93 --command -- curl -sS -o /dev/null -w '%{http_code}\n' <endpoint>/<bucket>`. `200`/`403` is a pass; a timeout is blocked egress (firewall, NAT, proxy) |
| `GENERIC_K8S_BUCKET_NOT_FOUND` | the bucket isn't `onehouse-customer-bucket-<link8>`, or was deleted | `oh dataplane storage status`; rerun `storage connect` (it reuses or creates the bucket) |
| `GENERIC_K8S_REQUIRED_EGRESS_UNREACHABLE` | the nodes can't reach a host Onehouse needs; the message names it | open outbound HTTPS to that host in the cluster's firewall or proxy |
| `GENERIC_K8S_ZOOKEEPER_UNREACHABLE` | the registered ZooKeeper doesn't answer from the cluster | [lock-provider.md](lock-provider.md): probe it, check `K -n zk get pods`; register it before `oh dataplane install` |
| `GENERIC_K8S_CLUSTER_TOKEN_LIFETIME_TOO_SHORT` | the API server caps service-account token lifetime below what Onehouse needs | the message names the setting to raise; on a managed control plane, ask the provider |

## Workflow tasks (`workflow.failed_task`)

`oh dataplane create` starts a workflow that installs the Onehouse components into the cluster. While it runs, `K get pods -A` shows each component's namespace appear.

| Task | What it does | Check first |
|---|---|---|
| `GENERIC_K8S_DP_CONFIG_CREATION` | preflight | the table above |
| `SET_DEFAULT_COMPUTE_CLUSTER` | sizes the default clusters from node types | "mixes node sizes": one `oh-instance-type` spans two shapes. `K get nodes -L oh-instance-type`; give each shape its own value |
| `docker-cache` | storage access setup | "s3 entry with no port": storage registered after the install; run `oh dataplane install` again |
| `VALIDATE_GENERIC_K8S_STORAGE` / `GENERIC_K8S_STORAGE_VALIDATION_FAILED` | Onehouse validates the bucket | the key was rotated or deleted, or lost access to the bucket: `oh dataplane storage rotate`. `AuthorizationHeaderMalformed` is a wrong signing region: rerun `storage connect` |
| `compute-clusters` | compute scheduling | `K get pods -A` for `Pending` pods; needs a node with `oh-essential-pods-vpc-cni=true` |
| `monitoring-stack-prep` / `monitoring-stack` | monitoring | PVC `Pending`: the default StorageClass can't provision. Pods `Pending` on memory: add essential capacity (`check_cluster.py` says how much) |
| `k8s-agent-deployer` | Onehouse agent | `K -n default get pods`; `Pending` means capacity or labels |
| `metrics-extractor` | the table metadata extractor | `K get pods -A | grep extractor`; CrashLoop: escalate |
| `REGISTER_GENERIC_K8S_LOCK_PROVIDER` / `GENERIC_K8S_LOCK_PROVIDER_REGISTRATION_FAILED` | registers the lock provider (ZooKeeper) | no ready ZooKeeper endpoint: `K -n zk get pods,endpoints`; probe it ([lock-provider.md](lock-provider.md)) |
| `GENERIC_K8S_SECRETS_DELETION_FAILED` (on drop) | removes the data plane's stored secrets | retry `oh dataplane drop`; escalate if it repeats |

Pods `Pending` with "Insufficient memory" or "Insufficient cpu" on any task: rerun
`check_cluster.py` and add what it says. A pod stuck on `/mnt is not a directory`: the node OS has no `/mnt`; on managed nodes, pick a node image that has it.

After fixing, run `oh dataplane create` again: completed tasks are skipped.

## Escalate

When a doc-conformant command fails and no row above applies, it's likely a platform issue. Help
the user open a support ticket (<https://docs.onehouse.ai/product/support>) with: the project ID, the
failing `oh` command and its full error, `oh -o json dataplane describe --org <project id>` output (it
holds no secrets), `oh -o json dataplane preflight` output, the workflow ID and failed task, and the
timestamp. Never include dp-connect values, the access key secret or a kubeconfig.

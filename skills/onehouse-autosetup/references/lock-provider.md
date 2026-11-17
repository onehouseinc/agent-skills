# Lock provider

Onehouse needs a lock provider for concurrent writers. On Self-Managed Kubernetes it is ZooKeeper. The data plane
registers it during `oh dataplane create`, from the servers recorded with
`oh dataplane set --zookeeper-servers`. A single-node ZooKeeper in the cluster is enough.

`K` below is `kubectl [--kubeconfig <kubeconfig>] --context <ctx>`, with the context the user named in Step 2.

## Detection, in order

Stop at the first one that answers.

1. **Already registered.** `oh -o json dataplane describe --org <project id>` shows `settings.zookeeper_servers`
   (and `settings.storage_class_name`).
   Probe each server. If one answers, there is nothing to do in this step.
2. **Already in the cluster.** `python3 <skill dir>/scripts/find_zookeeper.py --context <ctx> [--kubeconfig <kubeconfig>]`
   lists Services exposing port 2181 (best first) and ZooKeeper StatefulSets. Probe the
   candidates in order. The first that answers is the one to register.
3. **Ask the user** which to use: a ZooKeeper that answered, their own `host:port`, or a new
   single-node one installed from the bundled manifest (below). Recommend an existing one when
   one answered, the install otherwise.

### The probe

It starts a throwaway pod, so ask once before the first probe of the step:

```bash
K run oh-zk-probe --rm -i --restart=Never --image=busybox:1.36@sha256:73aaf090f3d85aa34ee199857f03fa3a95c8ede2ffd4cc2cdb5b94e566b11662 --command -- \
  sh -c 'sleep 2; echo srvr | nc -w 5 <host> <port>'
```

A reply starting `Zookeeper version:` with `Mode: standalone`, `leader` or `follower` is a pass. An
empty reply often means the server answers but `srvr` isn't whitelisted
(`4lw.commands.whitelist`); ask the user whether that ZooKeeper is theirs to use, and if so, to
add `srvr` to its whitelist. Don't change someone else's ZooKeeper.

`--rm` deletes the pod. If the run is interrupted, ask the user before deleting `oh-zk-probe`.

## Install ZooKeeper (only when the user picks it) [confirm]

The bundled `scripts/zookeeper.yaml` creates namespace `zk`, Service `zk` on 2181 and a
one-replica StatefulSet `zk` (image `zookeeper:3.9.6` pinned by digest, 100m CPU / 256 Mi requested, two 1 Gi volumes
from the default StorageClass, `nodeSelector: oh-essential-pods=true`). It needs the essential
labels from the node step first.

```bash
K apply -f <skill dir>/scripts/zookeeper.yaml
K -n zk rollout status sts/zk --timeout=300s
```

Then probe `zk.zk.svc.cluster.local 2181`.

| Symptom | Fix |
|---|---|
| `zk-0` `Pending`, PVC `Pending` | the default StorageClass can't provision: `K -n zk describe pvc`. Fix the class or make a working one the default |
| `zk-0` `Pending`, "didn't match node selector" | no node has `oh-essential-pods=true`: finish the node step |
| A provider rounds 1 Gi volumes up | fine |

## Register [confirm]

```bash
oh -o json dataplane set --org <project id> --zookeeper-servers <host:port>[,<host:port>] --storage-class <default class>
```

`oh dataplane set` updates only the settings it is given; the others keep their current values.
Pass `--storage-class` with the cluster's default class (`check_cluster.py` prints it as
`default_storage_class`) when `settings.storage_class_name` doesn't already match it. The output
is `{settings, updated_keys}`; check `settings` shows what you registered.
Register before `oh dataplane install`: it probes the registered servers.

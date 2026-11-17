# What a Self-Managed Kubernetes data plane needs

`scripts/check_cluster.py` checks the Kubernetes rows; `oh dataplane storage connect` checks the
storage rows. The customer provides the cluster, the object store and the access key. The skill
creates none of them.

## Kubernetes cluster

| Item | Requirement | Why |
|---|---|---|
| Access | a kubeconfig context with **cluster-admin** (`kubectl auth can-i '*' '*' -A` answers `yes`) | the setup installs cluster-wide components |
| Egress | outbound HTTPS from the nodes to Docker Hub, the Onehouse control plane and the storage endpoint. Nothing needs to be inbound | connections are outbound only; the preflight names any required host it can't reach |
| Version | a Kubernetes version your provider still supports; one minor behind the newest is safest | the components Onehouse installs lag new minors |
| Default StorageClass | **exactly one**, with dynamic provisioning and `ReadWriteOnce` | registered with `oh dataplane set --storage-class`. It backs about 56 Gi of volumes for monitoring and ZooKeeper |
| Essential nodes | labels `oh-essential-pods=true`, `oh-essential-pods-vpc-cni=true`, `monitoring-pods=true`; **at least 2 nodes, at least 4 vCPU each, at least 20 GiB allocatable memory in total** | the Onehouse control components and the monitoring stack request about 11.5 GiB, plus ZooKeeper and a metrics job |
| Monitoring | at least 1 node with `monitoring-pods=true` (the essential nodes carry it) | monitoring |
| Worker nodes | labels `driver-pods=true`, `job-workers=true`, `sql-workers=true`; at least 1 node with at least 4 vCPU and 10 GiB allocatable | Spark drivers and executors run here |
| Repair node | label `pool=job-workers-repair` (exactly that value, and **no** `job-workers` label); 1 node with at least 4 vCPU and 12 GiB allocatable (2 if you enable the metadata-table validation job) | some Onehouse table services run only here. Missing it fails the preflight with `GENERIC_K8S_REQUIRED_NODE_LABEL_MISSING` |
| Node type | `oh-instance-type=<value>` on **every** node that carries an Onehouse role label; one value per hardware shape | see [node-labelling.md](node-labelling.md). Missing it fails the preflight with `GENERIC_K8S_INSTANCE_TYPE_LABEL_MISSING` |
| Labels survive node replacement | set the labels at node-pool level where the provider supports it | `kubectl label` is lost when a node is replaced (upgrades, repairs) |
| `/mnt` | exists on the node OS | Onehouse components mount it |
| Autoscaling | fixed node counts are recommended | provider autoscaling isn't supported yet |

Not needed: a LoadBalancer, cloud IAM, VPC peering, or a public Kubernetes API endpoint.

A minimum layout that passes: 2 essential nodes of 4 vCPU / 16 GiB, 1 worker of 4 vCPU / 16 GiB
or larger, 1 repair node of 4 vCPU / 16 GiB.

Allocatable memory is what the kubelet reports in `status.allocatable.memory`, which is a few GiB
below the machine size. A 16 GiB machine usually reports about 12-13 GiB.

## Object storage

| Item | Requirement | Why |
|---|---|---|
| API | S3-compatible, SigV4, multipart upload | Onehouse reads and writes tables and its own state through the S3 API |
| Endpoint | `https://<dns-name>[:port]` with a **publicly trusted** certificate for that exact name. No IP address, no loopback, no self-signed certificate | Onehouse validates the form; the in-cluster probe uses the default trust store |
| Addressing | **path-style** (`https://<host>/<bucket>/...`) must work | Self-Managed Kubernetes projects use path-style everywhere |
| Signing region | optional: `us-east-1` when left blank; `storage connect` switches to the store's own region if it rejects that one | a wrong one fails with `AuthorizationHeaderMalformed` |
| Bucket | named exactly `onehouse-customer-bucket-<first 8 characters of the link UID>`; private; versioning not needed | Onehouse derives the name. `oh dataplane describe` prints it |
| Access key | a static access key / secret pair with read, write, list and delete on **that bucket**; scope it to that bucket if the provider can | stored encrypted by Onehouse. `storage connect` warns when the key can see other buckets |
| Reachability | from the cluster's nodes and from this machine | Onehouse checks it from the cluster; `storage connect` checks it from here |

# Node labelling

`scripts/check_cluster.py` proposes the plan; this file is the rulebook it follows, so you can
explain it and handle the cases it leaves to the user.

## The labels

| Role | Labels (all values exact) |
|---|---|
| essential (+ monitoring) | `oh-essential-pods=true`, `oh-essential-pods-vpc-cni=true`, `monitoring-pods=true` |
| workers | `driver-pods=true`, `job-workers=true`, `sql-workers=true` |
| repair | `pool=job-workers-repair`, and **no** `job-workers` label |
| every node with any role above (or an `openengine-*` label) | `oh-instance-type=<value for its hardware shape>` |

A node carries one role. Only `"true"` counts for the role labels: `monitoring-pods=false` is a
conflict, not a match.

## `oh-instance-type`: one value per hardware shape

Onehouse sizes Spark pods from node types, and a node type is the set of Ready nodes with the same
`oh-instance-type` value. So:

- **Same shape, same value.** A shape is equal vCPU capacity and memory capacity within 2 %. Two
  machines of the same size get the same value.
- **Different shapes, different values.** A value spanning two shapes is frozen at its last
  consistent size and raises an alert. Never reuse a value across shapes.
- The value must match `[a-z0-9]([a-z0-9.-]{0,61}[a-z0-9])?`.
- A node with no role label needs no `oh-instance-type`; it is invisible to Onehouse.

How the script picks the value for a shape, first match wins:

1. a valid value already on that shape's nodes (the most common one, if that value isn't mostly on
   another shape);
2. the provider's `node.kubernetes.io/instance-type` label, lowercased and sanitized, when every
   node of the shape carries the same one (`s-4vcpu-16gb`, `m5.xlarge`);
3. derived: `oh-<vcpu>cpu-<memory GiB>gi`, for example `oh-4cpu-16gi`.

## How roles are assigned

Only Ready nodes without a NoSchedule/NoExecute taint are considered. Existing role labels are
kept. Unlabelled nodes are filled, smallest first, in this order:

1. **repair**: the smallest node with at least 4 vCPU and 12 GiB allocatable, preferring one with no
   `pool` label;
2. **essential**: nodes with at least 4 vCPU until there are at least 2 with 20 GiB allocatable in
   total;
3. **workers**: a node with at least 4 vCPU and 10 GiB allocatable.

On a cluster with **no** Onehouse labels yet, every remaining node with at least 4 vCPU becomes a
worker. On a cluster that already has some, spare nodes are left alone. Say which nodes the plan
leaves unlabelled. The user may want a different split (for example a node reserved for other
workloads): rework the plan with them before applying it.

## Applying the plan

`label_plan.commands` are plain `kubectl label node` commands **without `--overwrite`**: they add
labels and fail rather than change one. Show them and run them after a yes.

`label_plan.conflict_commands` change or remove an existing label (`--overwrite`, or `key-` to
remove). **Never run one without asking about that node specifically**: show the node, the label,
the current value and the proposed one. A conflicting `pool` value or `oh-instance-type` usually
belongs to the user's own tooling. If they decline, pick another node or have them add one.

## Prefer node-pool labels

`kubectl label` is lost when the provider replaces a node (upgrade, repair, autoscaler). Where the
provider supports node-pool labels (EKS managed node groups, GKE, AKS, DOKS, most managed
offerings), tell the user to put the same labels on the pool, including `oh-instance-type`, and
give each pool one machine size. Some providers **replace** the pool's label set on update, so
the existing labels must be included. Apply with `kubectl label` now and recommend the pool-level
change as the durable fix.

After labelling, run `check_cluster.py` again: `label_plan.complete` must be `true`.

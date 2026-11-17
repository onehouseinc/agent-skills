# Teardown

Ask which parts to remove. Each item is a separate **[confirm]**: show the exact command, wait for
a yes, then run it. **Order matters:** dropping the data plane needs the cluster and its connection to Onehouse
alive, so it goes first.

1. **The data plane in Onehouse.**
   ```bash
   oh -o json dataplane drop --org <project id> --wait
   ```
   It removes the components Onehouse installed (monitoring, scheduling, the agent and so
   on) and the stored storage credentials. Clusters, jobs and flows in the project stop working.
   List them first (`oh cluster show`, `oh flow show`, `oh job show`) and include them in the
   confirmation. On failure, report `workflow.failed_task` and stop; don't force it.
   Dropping the data plane does **not** delete the Onehouse project; that's done in the console.

2. **dp-connect.**
   ```bash
   H -n <namespace> uninstall <release> --wait
   ```
   Release and namespace are the ones `oh dataplane install` returned (`byok-dp-connect`,
   `oh-dpconnect`). Skip it if the user is deleting the cluster anyway.

3. **ZooKeeper (optional), only if this skill installed it** (namespace `zk` from the bundled
   manifest). A ZooKeeper the user already had is theirs; never remove it.
   ```bash
   K delete -f <skill dir>/scripts/zookeeper.yaml
   ```
   This deletes namespace `zk` and its volumes.

4. **Node labels (optional).** Removing them is harmless once the data plane is dropped. Build the
   command from the labels the plan applied:
   `K label node <node> oh-essential-pods- ...`.

5. **The bucket: never without explicit confirmation.** It holds the data plane's state and every
   table written to it. The skill never holds the access key, so it can't and doesn't delete
   it. If the user wants it gone, tell them to empty and delete `onehouse-customer-bucket-<link8>`
   with their provider's console or CLI, and to delete or revoke the access key afterwards. Make
   sure they understand it is irreversible.

6. **Local files.** If the progress file records a `values_dir` from an older run and it still
   exists under the system temp folder, delete it (`rm -rf <values_dir>`).

The cluster, node pools and the object store belong to the user, and this skill never deletes
them. End by listing what is left running.

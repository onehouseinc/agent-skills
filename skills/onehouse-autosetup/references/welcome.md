# Welcome message

Show the block below to the user at the start, as written. Fill nothing in. It's what they need
ready before setup starts.

---

**Set up a Onehouse data plane on your own Kubernetes cluster**

This connects a Kubernetes cluster and S3-compatible storage that you already run to your Onehouse
project. Expect 1–2 hours, mostly waiting for installs. I'll show every command first and ask before
I change anything. The only things I might create are the Onehouse storage bucket and, if you
want one, a small ZooKeeper as the lock provider, and each only if it doesn't already exist.

**What you need**

1. **A Onehouse project**
   - Created in the Onehouse console with the **Self-Managed Kubernetes** cloud (Enterprise plan).
   - Its **project ID**, a UUID such as `12345678-90ab-cdef-1234-567890abcdef`. The `oh` CLI calls
     it `--org`.

2. **A Kubernetes cluster you administer**
   - A `kubectl` context with **cluster-admin** on it.
   - **Exactly one default StorageClass**, with dynamic provisioning.
   - **Outbound HTTPS** from the nodes. Nothing needs to reach the cluster from outside.
   - **Nodes.** Sizes are *allocatable* resources, a few GiB below the machine size. I'll add the
     labels for you.

     | Role | Nodes | Each node | Labels |
     |---|---|---|---|
     | Essential | 2 or more | 4+ vCPU; 20+ GiB across the group | `oh-essential-pods`, `monitoring-pods` |
     | Worker | 1 or more | 4+ vCPU, 10+ GiB | `driver-pods`, `job-workers`, `sql-workers` |
     | Repair | 1 | 4+ vCPU, 12+ GiB | `pool=job-workers-repair` |

     The smallest layout that works is 4 nodes of 4 vCPU / 16 GiB. Set the labels at the node-pool
     level if your provider allows it, so they survive node replacement.

3. **S3-compatible object storage**
   - An **HTTPS endpoint** with a publicly trusted certificate. IP addresses and self-signed
     certificates won't work.
   - An **access key and secret** with read, write, list and delete on the bucket. You'll type the
     secret into a form in your browser on this machine. **Never paste it into this chat.**
   - **The bucket.** It must be named `onehouse-customer-bucket-<first 8 characters of your link
     ID>`; I'll tell you the exact name. I can create it, or you can create it first.
   - **Region: not needed.** Self-Managed Kubernetes projects don't use a region. If you leave it blank,
     `us-east-1` is used, and the storage check detects your store's signing region on its own.

4. **On this machine**
   - The `oh` CLI **0.7.0 or later**. I'll check the version and offer to install a pinned,
     hash-checked release.
   - `kubectl`, `helm` 3.8 or later, and `python3`.
   - A web browser, for `oh login` and the storage form.

**Next, I'll ask for your Onehouse project ID.**

# Chapter 15 — Storage

Containers are ephemeral; data is not. Kubernetes separates *what a pod asks for* from *what
actually provides it*, which makes manifests portable across clusters but adds a layer of
indirection that is worth understanding before it fails.

## Volumes that are not persistent

Not everything needs a PersistentVolume.

**`emptyDir`** — a directory created when the pod is scheduled and deleted when the pod is
removed. Shared between containers in the pod (Chapter 5).

```yaml
volumes:
  - name: tmp
    emptyDir: {}
  - name: cache
    emptyDir:
      medium: Memory        # tmpfs — RAM, not disk
      sizeLimit: 256Mi
```

This is what the `pingd` Deployment uses to give a `readOnlyRootFilesystem` container a
writable `/tmp` — the same pattern as the Docker book's `--read-only --tmpfs /tmp`.

Two cautions: `emptyDir` consumes the node's **ephemeral storage**, and a pod filling the disk
triggers `DiskPressure` and evicts other pods (Chapter 20) — set `sizeLimit`. And
`medium: Memory` counts against the container's **memory limit**, so a large tmpfs can get you
OOM-killed.

**`configMap`, `secret`, `downwardAPI`, `projected`** — covered in Chapter 7. `projected`
combines several into one directory, which is how service account tokens are mounted.

**`hostPath`** — mounts a path from the node.

```yaml
volumes:
  - name: docker-sock
    hostPath:
      path: /var/run/containerd/containerd.sock
      type: Socket
```

**`hostPath` is a privilege escalation waiting to happen.** A pod that can mount
`/var/lib/kubelet` or the container runtime socket effectively owns the node, and mounting
`/etc` or `/` gives host root. Legitimate uses are node-level agents (log shippers, CSI
drivers, monitoring) — and those are exactly what an attacker would like to impersonate.
Restrict it with Pod Security Standards or a policy engine (Chapters 23–24). For pod-local
scratch, use `emptyDir`; for durable data, use a PVC.

## PersistentVolumes and PersistentVolumeClaims

The core abstraction:

- A **PersistentVolumeClaim (PVC)** is a request: "I need 10Gi, ReadWriteOnce."
- A **PersistentVolume (PV)** is the actual storage.
- A **StorageClass** describes *how* to provision PVs dynamically.

Pods reference PVCs, never PVs. That indirection is what makes a manifest portable: the same
PVC gets an EBS volume on AWS, a PD on GCP, and a local directory on kind.

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: pingd-data
spec:
  accessModes: ["ReadWriteOnce"]
  storageClassName: standard
  resources:
    requests:
      storage: 10Gi
```

```yaml
    volumeMounts:
      - name: data
        mountPath: /var/lib/pingd
volumes:
  - name: data
    persistentVolumeClaim:
      claimName: pingd-data
```

## StorageClasses

```bash
kubectl get storageclass
```

```
NAME                 PROVISIONER             RECLAIMPOLICY   VOLUMEBINDINGMODE      ALLOWVOLUMEEXPANSION
standard (default)   rancher.io/local-path   Delete          WaitForFirstConsumer   false
```

Four columns, all of which matter:

**PROVISIONER** — the CSI driver. `rancher.io/local-path` here; `ebs.csi.aws.com`,
`pd.csi.storage.gke.io` elsewhere.

**RECLAIMPOLICY** — what happens to the PV when its PVC is deleted. `Delete` **destroys the
underlying volume and its data**. `Retain` keeps it for manual recovery. Most default classes
use `Delete`, which means deleting a PVC deletes your data with no confirmation. For anything
you care about, use a class with `Retain`, or make sure your backups are real.

**VOLUMEBINDINGMODE** — `Immediate` provisions as soon as the PVC is created;
`WaitForFirstConsumer` waits until a pod using it is scheduled. **`WaitForFirstConsumer` is
almost always what you want** in a multi-zone cluster: with `Immediate`, a volume can be
created in zone A while the pod is later scheduled to zone B, and the pod is then permanently
`Pending` because the volume cannot follow it.

**ALLOWVOLUMEEXPANSION** — whether you can grow a PVC later by editing
`spec.resources.requests.storage`. `false` here. If your class supports it, expansion is
online for most drivers; **shrinking is never supported**.

Note `(default)`: a PVC with no `storageClassName` uses the default class. A PVC with
`storageClassName: ""` explicitly requests *no* class, meaning static binding only — a
subtle distinction that produces permanently `Pending` PVCs.

## Access modes

| Mode | Meaning |
|---|---|
| `ReadWriteOnce` (RWO) | Read-write by **one node** |
| `ReadOnlyMany` (ROX) | Read-only by many nodes |
| `ReadWriteMany` (RWX) | Read-write by many nodes |
| `ReadWriteOncePod` (RWOP) | Read-write by exactly **one pod** |

Two clarifications worth having.

**RWO is per node, not per pod.** Two pods on the *same* node can both mount an RWO volume.
If you need a hard single-writer guarantee — and for most databases you do —
`ReadWriteOncePod` is the mode that actually provides it.

**RWX is not widely available.** Block storage (EBS, GCP PD, Azure Disk) is RWO only. RWX
needs a filesystem: NFS, EFS, Azure Files, CephFS. Writing `ReadWriteMany` against a block
storage class gives you a PVC that never binds. If your design requires many pods writing one
volume, reconsider — object storage is usually the better answer.

## The lifecycle

```
PVC created (Pending)
  → StorageClass provisions a PV, or an existing PV matches
  → PVC Bound to PV
  → pod schedules, volume attaches to the node, mounts into the container
  → pod deleted → volume unmounts and detaches; PVC and PV survive
  → PVC deleted → reclaim policy: Delete (destroy) or Retain (keep, status Released)
```

A `Released` PV is **not automatically reusable** — it still references the old claim. Making
it available again means clearing `spec.claimRef` by hand. This surprises people expecting
`Retain` to mean "reuse".

## Volume snapshots

```yaml
apiVersion: snapshot.storage.k8s.io/v1
kind: VolumeSnapshot
metadata:
  name: pingd-data-snapshot
spec:
  volumeSnapshotClassName: csi-snapshotter
  source:
    persistentVolumeClaimName: pingd-data
```

Then restore by creating a PVC with `dataSource` pointing at the snapshot.

Requires the snapshot CRDs and a CSI driver that supports it. And the caveat that matters:
**a snapshot of a running database is crash-consistent, not application-consistent** — the
same warning as `tar`-ing a live data directory in the Docker book. For a database, use its
own backup tooling, or quiesce it first.

## Stateful workloads, honestly

Kubernetes can run databases. The question is whether it should.

**Reasons to use a managed database:** backups, point-in-time recovery, failover, version
upgrades, and connection scaling are solved problems you are buying rather than building. The
operational surface you avoid is enormous, and the failure modes are someone else's pager.

**If you do run it in-cluster:**

- Use a **mature operator** — CloudNativePG, Zalando's postgres-operator, Percona,
  Strimzi for Kafka. The operator is where the backup, failover and upgrade logic lives, and
  that logic is the actual product. A hand-written StatefulSet gives you a database that runs
  and no story for what happens when it does not.
- Use `ReadWriteOncePod` where supported.
- Use fast local storage (local PVs or NVMe-backed classes) — network storage latency is
  often the limiting factor for database performance.
- Set a **PodDisruptionBudget** (Chapter 25) so a node drain cannot take your quorum.
- Test restores. Untested backups are a hypothesis.

The middle path many teams settle on: stateless workloads in Kubernetes, stateful ones
managed. That is not a failure of nerve; it is an accurate assessment of where the operational
risk lies.

## Try it

Look at what your cluster provides:

```bash
kubectl get storageclass
```

```
standard (default)   rancher.io/local-path   Delete   WaitForFirstConsumer   false
```

Note `WaitForFirstConsumer` — create a PVC and watch it stay `Pending` on purpose:

```bash
kubectl apply -f examples/manifests/15-pvc.yaml && kubectl get pvc pingd-data
```

```
NAME         STATUS    VOLUME   CAPACITY   STORAGECLASS
pingd-data   Pending                       standard
```

That is not an error. `kubectl describe pvc pingd-data` says so:

```bash
kubectl describe pvc pingd-data | grep -A3 Events
```

```
WaitForFirstConsumer  waiting for first consumer to be created before binding
```

Now create a pod that uses it, and watch it bind:

```bash
kubectl apply -f examples/manifests/15-pod-with-pvc.yaml && sleep 15 && kubectl get pvc pingd-data
```

Write something, then prove it survives the pod:

```bash
kubectl exec pvc-demo -- sh -c 'echo "written at $(date -u +%T)" > /data/note.txt; cat /data/note.txt'
```

```bash
kubectl delete pod pvc-demo && kubectl apply -f examples/manifests/15-pod-with-pvc.yaml && sleep 12
```

```bash
kubectl exec pvc-demo -- cat /data/note.txt
```

Same content — the pod was destroyed and recreated; the data was not.

See where it actually lives on the node:

```bash
kubectl get pv -o custom-columns='NAME:.metadata.name,CLAIM:.spec.claimRef.name,RECLAIM:.spec.persistentVolumeReclaimPolicy,PATH:.spec.local.path'
```

Note `RECLAIM: Delete`. Clean up — **⚠️ destructive: with this reclaim policy, deleting the
PVC destroys the data permanently**:

```bash
kubectl delete pod pvc-demo && kubectl delete pvc pingd-data
```

```bash
kubectl get pv
```

```
pvc-f4790ebf-...   100Mi   RWO   Delete   Released   default/pingd-data
```

It passes through `Released` first — reclamation is asynchronous — and the provisioner
removes it shortly after. Watch it disappear:

```bash
sleep 30 && kubectl get pv
```

That transient `Released` state is worth recognising: a PV stuck in `Released` for a long
time means the provisioner is not reclaiming it, which on a `Retain` class is expected and on
a `Delete` class means something is wrong.

## Takeaways

- `emptyDir` for pod-local scratch — it counts against ephemeral storage, and
  `medium: Memory` counts against the **memory limit**.
- **`hostPath` is a privilege escalation risk.** Restrict it by policy.
- Pods reference PVCs, never PVs. That indirection is what makes manifests portable.
- **`reclaimPolicy: Delete` destroys your data when the PVC is deleted**, with no
  confirmation. Most default classes use it.
- **`WaitForFirstConsumer` avoids provisioning a volume in the wrong zone.** Prefer it.
- RWO is **per node**, not per pod — use `ReadWriteOncePod` for a real single-writer
  guarantee. RWX needs a file-based backend and is unavailable on block storage.
- A `Retain`ed PV is `Released`, not reusable — clear `claimRef` to reuse it.
- Volume snapshots are crash-consistent, not application-consistent.
- Prefer managed databases. If in-cluster, use a mature operator, a PDB, and tested restores.

---

Previous: [Chapter 14 — NetworkPolicy](14-networkpolicy.md) ·
Next: [Chapter 16 — The debugging mindset](16-debugging-mindset.md)

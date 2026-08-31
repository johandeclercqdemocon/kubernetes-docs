# Chapter 10 — The other workload kinds

Deployments cover stateless services, which is most of what most clusters run. Four other
controllers cover the rest, and choosing the wrong one produces problems that are hard to
back out of later.

## Job

Runs pods until a specified number complete successfully, then stops.

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: migrate
spec:
  backoffLimit: 4
  completions: 1
  template:
    spec:
      restartPolicy: OnFailure       # or Never. Never `Always`.
      containers:
        - name: migrate
          image: pingd:latest
          command: ["alembic", "upgrade", "head"]
```

Measured on a trivial job:

```bash
kubectl create job demo-job --image=busybox:1.37 -- sh -c 'echo working; sleep 3; echo done'
```

```bash
kubectl get job demo-job -o custom-columns='NAME:.metadata.name,COMPLETIONS:.status.succeeded,STATUS:.status.conditions[0].type'
```

```
NAME       COMPLETIONS   STATUS
demo-job   1             SuccessCriteriaMet
```

```bash
kubectl get pod -l job-name=demo-job
```

```
NAME             STATUS      RESTART POLICY
demo-job-lfw4m   Succeeded   Never
```

Note that **the completed pod is retained**. That is deliberate — its logs are the record of
what happened — and it is also how clusters accumulate thousands of `Succeeded` pods. Clean
up automatically:

```yaml
spec:
  ttlSecondsAfterFinished: 3600
```

Set this on every Job. Without it, the pods stay until something deletes them.

The fields that matter:

- **`backoffLimit`** (default 6) — how many pod failures before the Job is marked `Failed`.
  Failures back off exponentially, capped at 6 minutes.
- **`activeDeadlineSeconds`** — a wall-clock cap on the whole Job. This overrides
  `backoffLimit`; when it expires the Job fails and running pods are killed. Essential for
  anything that could hang.
- **`completions` and `parallelism`** — for parallel work. `completions: 10, parallelism: 3`
  runs ten tasks, three at a time.
- **`completionMode: Indexed`** — each pod gets `JOB_COMPLETION_INDEX` in its environment, so
  workers can shard deterministically. This is what makes Jobs usable for partitioned batch
  work, and it is underused.

`restartPolicy` must be `OnFailure` or `Never` — `Always` is rejected, because a Job that
always restarts can never complete. The distinction: `OnFailure` restarts the container in
place (same pod, keeps the node and any local scratch); `Never` creates a new pod (fresh
everything, and you keep the failed pod for inspection). For debugging, `Never` is more
useful.

**Sidecars used to break Jobs**: a never-exiting sidecar meant the pod never completed. The
native sidecar mechanism from Chapter 5 (`initContainers` with `restartPolicy: Always`) fixes
this — sidecars are terminated once the main containers finish.

For the LLM evaluation runner case, the Job fields that matter are `activeDeadlineSeconds`
(an API call that hangs must not hold a pod forever), `backoffLimit: 0` where retries would
double-charge for inference, and `Indexed` completion mode to shard a test suite across
workers deterministically.

## CronJob

Creates Jobs on a schedule.

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: nightly-report
spec:
  schedule: "0 3 * * *"
  timeZone: "Europe/Brussels"
  concurrencyPolicy: Forbid
  startingDeadlineSeconds: 300
  successfulJobsHistoryLimit: 3
  failedJobsHistoryLimit: 3
  jobTemplate:
    spec:
      ttlSecondsAfterFinished: 3600
      template:
        spec:
          restartPolicy: OnFailure
          containers:
            - name: report
              image: pingd:latest
              command: ["python", "-m", "reports.nightly"]
```

The fields that cause incidents when omitted:

**`concurrencyPolicy`** defaults to `Allow`. If a run takes longer than the interval, you get
overlapping executions — two copies of your nightly report writing to the same table. Use
`Forbid` (skip if one is running) or `Replace` (kill the old one) unless overlap is genuinely
safe.

**`timeZone`** — without it, schedules are interpreted in the **controller manager's** time
zone, usually UTC. A "3am" job running at 4am or 2am depending on daylight saving is a
recurring surprise. Set it explicitly.

**`startingDeadlineSeconds`** — if the controller was down at the scheduled time, how late is
too late to still run? Without it, a controller outage can trigger a burst of missed runs at
once. Note that if more than 100 schedules are missed, the CronJob stops scheduling entirely
and logs an error — a genuinely surprising failure mode that leaves you with a CronJob that
silently never runs again.

**History limits** — defaults are 3 and 1. Reasonable, but Jobs plus their pods still
accumulate; combine with `ttlSecondsAfterFinished`.

CronJob guarantees are **at-least-once, approximately**. A job may run twice or be skipped.
Make the work idempotent; do not build anything requiring exactly-once semantics on a
CronJob alone.

## DaemonSet

One pod per node, automatically, including nodes added later.

```yaml
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: node-exporter
spec:
  selector:
    matchLabels: { app: node-exporter }
  template:
    metadata:
      labels: { app: node-exporter }
    spec:
      tolerations:
        - operator: Exists            # run on every node, including tainted ones
      containers:
        - name: node-exporter
          image: quay.io/prometheus/node-exporter:v1.8.2
```

```bash
kubectl get ds -n kube-system kube-proxy
```

```
NAME         DESIRED   READY
kube-proxy   3         3
```

Three nodes, three pods, maintained automatically.

Used for things that are properly per-node: log shippers, metrics exporters, CNI agents,
storage drivers, security agents. Restrict to a subset with `nodeSelector` — GPU monitoring
only on GPU nodes.

The **tolerations** point matters: by default a DaemonSet will not schedule onto tainted
nodes, including control-plane nodes. Infrastructure DaemonSets usually want
`tolerations: [{operator: Exists}]` to run everywhere regardless of taints. Forgetting this
means your monitoring silently has a hole exactly where you need it.

DaemonSets support `RollingUpdate` with `maxUnavailable`, and updating one means touching
every node — do it carefully on large clusters.

## StatefulSet

For workloads where pods are **not** interchangeable.

```yaml
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: postgres
spec:
  serviceName: postgres-headless     # required: a headless Service
  replicas: 3
  selector:
    matchLabels: { app: postgres }
  template:
    metadata:
      labels: { app: postgres }
    spec:
      containers:
        - name: postgres
          image: postgres:17-alpine
          volumeMounts:
            - name: data
              mountPath: /var/lib/postgresql/data
  volumeClaimTemplates:              # one PVC per pod, created automatically
    - metadata:
        name: data
      spec:
        accessModes: ["ReadWriteOnce"]
        resources:
          requests:
            storage: 10Gi
```

What you get that a Deployment does not give you:

**Stable, ordinal names.** `postgres-0`, `postgres-1`, `postgres-2` — not random suffixes.
A pod that is replaced comes back with the *same* name.

**Stable network identity.** With the required headless Service, each pod gets a predictable
DNS name: `postgres-0.postgres-headless.default.svc.cluster.local`. This is what lets cluster
members find each other.

**Stable storage.** `volumeClaimTemplates` creates one PVC per pod, and the PVC follows the
pod name. `postgres-0` always gets `data-postgres-0`, even after rescheduling to another
node.

**Ordered operations.** Pods are created 0, 1, 2 — each waiting for the previous to be Ready
— and deleted in reverse. Set `podManagementPolicy: Parallel` if you do not need this and
want faster scaling.

The consequences people are unprepared for:

- **PVCs are not deleted** when you delete the StatefulSet or scale it down. That is
  deliberate (it is your data), and it means scaling from 5 to 3 and back to 5 reattaches the
  original volumes. `persistentVolumeClaimRetentionPolicy` (stable since v1.32) lets you
  change this.
- **Rollouts are slow**, one pod at a time in reverse ordinal order, waiting for readiness at
  each step.
- **A stuck pod blocks everything.** If `postgres-1` will not become Ready, the rollout stops
  there permanently. `podManagementPolicy: Parallel` and partitioned rollouts help.

**Do not use a StatefulSet for a stateless service.** It is slower to roll out, slower to
scale, and gives you guarantees you are paying for and not using. The only reasons are stable
identity, stable per-pod storage, or ordered startup.

And the larger question: **should you run the database in Kubernetes at all?** A managed
database removes an entire category of operational risk — backups, failover, upgrades,
point-in-time recovery — for money. If you do run it yourself, use a mature **operator**
(CloudNativePG, Zalando, Percona) rather than a hand-written StatefulSet; the operator is
where the backup, failover and upgrade logic lives, and that logic is the actual product.
Chapter 30 covers operators.

## ReplicaSet

Mentioned for completeness. You do not create these directly — Deployments manage them
(Chapter 6). The only time you interact with one is reading `kubectl get rs` output during a
rollout.

## Choosing

| Need | Use |
|---|---|
| Stateless service, interchangeable replicas | **Deployment** |
| Work that finishes | **Job** |
| Work that finishes, on a schedule | **CronJob** |
| Exactly one pod per node | **DaemonSet** |
| Stable identity, per-pod storage, or ordered startup | **StatefulSet** |
| A database | **A managed service**, or a StatefulSet driven by an operator |

## Try it

Run a Job and see what it leaves behind:

```bash
kubectl create job demo-job --image=busybox:1.37 -- sh -c 'echo working; sleep 3; echo done'
```

```bash
sleep 12 && kubectl get job,pods -l job-name=demo-job
```

The pod is `Succeeded`, not deleted — its logs are still readable:

```bash
kubectl logs -l job-name=demo-job
```

```bash
kubectl delete job demo-job
```

Deleting the Job deletes its pods, via the ownership chain from Chapter 2.

Now a Job that fails, to see `backoffLimit` at work:

```bash
kubectl create job failjob --image=busybox:1.37 -- sh -c 'echo attempting; exit 1' --dry-run=client -o yaml | sed 's/^spec:/spec:\n  backoffLimit: 2/' | kubectl apply -f -
```

```bash
sleep 45 && kubectl get job failjob -o custom-columns='NAME:.metadata.name,FAILED:.status.failed,CONDITION:.status.conditions[0].type'
```

```bash
kubectl delete job failjob
```

Look at a real DaemonSet and confirm it tracks node count:

```bash
kubectl get ds -n kube-system -o custom-columns='NAME:.metadata.name,DESIRED:.status.desiredNumberScheduled,READY:.status.numberReady'
```

`kube-proxy` shows 3 desired on a 3-node cluster. Note which DaemonSets tolerate the
control-plane taint and which do not:

```bash
kubectl get ds -n kube-system kube-proxy -o jsonpath='{.spec.template.spec.tolerations[*].operator}{"\n"}'
```

Finally, see StatefulSet identity. This uses the local-path provisioner kind ships with:

```bash
kubectl apply -f examples/manifests/10-statefulset.yaml
```

```bash
kubectl rollout status statefulset/web --timeout=180s && kubectl get pods -l app=web
```

Ordinal names, `web-0` and `web-1`, and one PVC each:

```bash
kubectl get pvc -l app=web
```

Delete a pod and watch it return **with the same name and the same volume**:

```bash
kubectl delete pod web-0 && sleep 15 && kubectl get pod web-0 && kubectl get pvc -l app=web
```

Clean up — note the PVCs survive deliberately. **⚠️ destructive** on the second command:

```bash
kubectl delete -f examples/manifests/10-statefulset.yaml
```

```bash
kubectl delete pvc -l app=web
```

## Takeaways

- **Job**: set `ttlSecondsAfterFinished` on every one, or completed pods accumulate forever.
  `activeDeadlineSeconds` for anything that could hang. `Indexed` mode for sharded work.
- **CronJob**: `concurrencyPolicy` defaults to `Allow` (overlapping runs), `timeZone` defaults
  to the controller's, and missing 100 schedules stops it permanently. Make the work
  idempotent.
- **DaemonSet**: one per node automatically. Infrastructure DaemonSets need
  `tolerations: [{operator: Exists}]` or they silently skip tainted nodes.
- **StatefulSet**: stable names, stable DNS, stable per-pod PVCs, ordered operations. PVCs
  survive deletion on purpose; a stuck pod blocks the whole rollout.
- Do not use a StatefulSet for a stateless service.
- For databases, prefer a managed service, or an operator over a hand-rolled StatefulSet.

---

Previous: [Chapter 9 — Health and lifecycle](09-health-and-lifecycle.md) ·
Next: [Chapter 11 — Services](11-services.md)

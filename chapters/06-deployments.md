# Chapter 6 — Deployments and ReplicaSets

A Deployment is how you run a stateless service. It manages ReplicaSets, which manage Pods,
and the indirection exists for exactly one reason: **rollouts**.

## The two-level structure

```
Deployment  ──manages──►  ReplicaSet  ──manages──►  Pods
  (rollout strategy)        (replica count)
```

- The **ReplicaSet controller** does one thing: keep N pods matching a selector alive. It has
  no concept of versions or updates.
- The **Deployment controller** creates a *new* ReplicaSet whenever the pod template changes,
  then shifts replicas from old to new according to your strategy.

You can see both after an update:

```bash
kubectl get rs -l app=pingd -o custom-columns='NAME:.metadata.name,DESIRED:.spec.replicas,CURRENT:.status.replicas,REVISION:.metadata.annotations.deployment\.kubernetes\.io/revision'
```

```
NAME               DESIRED   CURRENT   REVISION
pingd-5975cc6496   0         0         1
pingd-79fd4dd7cc   3         3         2
```

The old ReplicaSet is **kept at zero replicas**, not deleted. That is what makes rollback
instant: rolling back does not rebuild anything, it just scales the old ReplicaSet back up.
`revisionHistoryLimit` (default 10) controls how many are retained.

The hash in the name (`5975cc6496`) is computed from the pod template. Identical templates
produce identical hashes, which is why re-applying an unchanged manifest does not trigger a
rollout.

## Rollouts

```yaml
spec:
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 1
      maxUnavailable: 0
```

- **`maxSurge`** — how many pods above `replicas` may exist during the update.
- **`maxUnavailable`** — how many below `replicas` may be unavailable.

Both accept counts or percentages. The defaults are `25%` for each, which means a default
Deployment **goes below capacity during every rollout**. For a service under load, that is
usually wrong.

`maxSurge: 1, maxUnavailable: 0` is the safe production setting: start one new pod, wait for
it to be *ready*, then remove one old pod. Capacity never dips. The cost is that you need
room for one extra pod and the rollout is slightly slower.

`maxUnavailable: 0` with `maxSurge: 0` is invalid — nothing could ever happen — and the API
server rejects it.

Watch a rollout:

```bash
kubectl rollout status deployment/pingd
```

```
deployment "pingd" successfully rolled out
```

This blocks until complete and **exits non-zero on failure**, which makes it the right thing
to put in a deploy script. Without it, your pipeline reports success the instant the API
accepts the object, long before any pod is running.

Readiness gates the rollout. A new pod that never becomes ready stops the rollout rather
than replacing everything with something broken — but only if you have a readiness probe.
Without one, a pod is "ready" as soon as its container starts, and a completely broken
version rolls out to 100% happily. **This is the most important reason to have readiness
probes** (Chapter 9).

Rollouts also have a deadline:

```yaml
spec:
  progressDeadlineSeconds: 600
```

After that with no progress, the Deployment gets `Progressing=False` with reason
`ProgressDeadlineExceeded`. Note what it does *not* do: **it does not roll back
automatically.** The rollout simply stops, half-done, and waits for you. Automatic rollback
requires a higher-level tool (Chapter 27).

## Rollback

```bash
kubectl rollout history deployment/pingd
```

```
REVISION  CHANGE-CAUSE
1         kubectl set image deployment/pingd api=pingd:latest --record=true
2         kubectl set image deployment/pingd api=pingd:latest --record=true
```

```bash
kubectl rollout undo deployment/pingd
```

```bash
kubectl rollout undo deployment/pingd --to-revision=3
```

Measured: after an `undo`, the environment variable set in revision 2 was gone and the value
from revision 1 was back:

```
PINGD_VERSION now = 1.0.0
```

Two caveats. `CHANGE-CAUSE` comes from an annotation set by the deprecated `--record` flag,
and as the output above shows, it is frequently useless — both revisions have the same
description because `set image` was run twice with different arguments elsewhere. Set it
yourself if you want it to mean anything:

```bash
kubectl annotate deployment/pingd kubernetes.io/change-cause="Deploy 1.4.2 (commit a1b2c3d)"
```

And more importantly: **if you deploy from git, `kubectl rollout undo` puts the cluster out
of sync with your manifests.** The next `apply` or GitOps sync re-applies the broken version.
Rollback in git, not in the cluster — or accept that `undo` is an emergency stop that you
must immediately follow with a git revert.

## Pausing

```bash
kubectl rollout pause deployment/pingd
```

Make several changes without each triggering a rollout, then:

```bash
kubectl rollout resume deployment/pingd
```

Also useful mid-rollout: pause when you see errors, investigate with old and new pods both
running, then resume or undo.

## The selector trap

```yaml
spec:
  selector:
    matchLabels:
      app: pingd
  template:
    metadata:
      labels:
        app: pingd     # must match the selector
```

**`spec.selector` is immutable.** Try to change it:

```
The Deployment "pingd" is invalid:
* spec.template.metadata.labels: Invalid value: {"app":"pingd"}: `selector` does not match template `labels`
* spec.selector: Invalid value: {"matchLabels":{"app":"pingd2"}}: field is immutable
```

You cannot change it, ever. The only path is delete and recreate the Deployment, which means
downtime unless you do a careful parallel migration.

So **choose selector labels that will never need to change**. Specifically: do not put a
version in the selector. `app: pingd` is right; `app: pingd, version: 1.4.2` guarantees you
must delete the Deployment on every release.

Keep the selector minimal and stable, and put everything descriptive in the template's
labels — those *can* change:

```yaml
spec:
  selector:
    matchLabels:
      app.kubernetes.io/name: pingd        # stable forever
  template:
    metadata:
      labels:
        app.kubernetes.io/name: pingd      # required: matches selector
        app.kubernetes.io/version: "1.4.2" # free to change
```

A second trap: **two Deployments with overlapping selectors fight.** Each ReplicaSet adopts
any pod matching its selector, so they will repeatedly delete each other's pods. The symptom
is pods being created and destroyed continuously with no obvious cause. Selectors must be
disjoint.

## Scaling

```bash
kubectl scale deployment pingd --replicas=5
```

```bash
kubectl autoscale deployment pingd --min=3 --max=10 --cpu-percent=70
```

If an HPA manages a Deployment, **remove `replicas` from your manifest** — otherwise every
`apply` resets the count the HPA just calculated, and the two fight. Chapter 25 covers this
properly; it is a common and confusing production problem.

## Restarting without changing anything

There is no `kubectl restart deployment`. There is:

```bash
kubectl rollout restart deployment/pingd
```

It works by setting an annotation (`kubectl.kubernetes.io/restartedAt`) on the pod template,
which changes the template hash, which creates a new ReplicaSet, which rolls pods gracefully.
Use it to pick up a changed ConfigMap or Secret (Chapter 7), or to clear a bad in-memory
state.

Deleting pods to "restart" a Deployment works too, but it is abrupt and ignores your rollout
strategy. Prefer `rollout restart`.

## Deployment vs the alternatives

Use a **Deployment** when pods are interchangeable: stateless services, workers, anything
where "replica 2" means nothing in particular.

Use a **StatefulSet** (Chapter 10) when pods need stable identity, stable storage, or
ordered startup — databases, clustered systems, anything doing leader election.

Use a **DaemonSet** for one pod per node: log shippers, node exporters, CNI agents.

Use a **Job** or **CronJob** for work that finishes.

The mistake worth avoiding is a StatefulSet used for a stateless service because it "sounds
more serious". It is slower to roll out, harder to scale, and gives you ordering guarantees
you do not need.

## Try it

Watch a rollout create a second ReplicaSet:

```bash
kubectl get rs -l app=pingd
```

```bash
kubectl set env deployment/pingd PINGD_VERSION=2.0.0 && kubectl rollout status deployment/pingd
```

```bash
kubectl get rs -l app=pingd -o custom-columns='NAME:.metadata.name,DESIRED:.spec.replicas,REVISION:.metadata.annotations.deployment\.kubernetes\.io/revision'
```

Two ReplicaSets, the old one scaled to 0 and retained. Roll back and confirm:

```bash
kubectl rollout undo deployment/pingd && kubectl rollout status deployment/pingd
```

```bash
kubectl get deployment pingd -o jsonpath='{.spec.template.spec.containers[0].env[0].value}{"\n"}'
```

Prove the selector is immutable:

```bash
kubectl patch deployment pingd --type=json -p='[{"op":"replace","path":"/spec/selector/matchLabels/app","value":"pingd2"}]'
```

```
* spec.selector: Invalid value: {"matchLabels":{"app":"pingd2"}}: field is immutable
```

Watch capacity during a rollout with `maxUnavailable: 0` — ready count never drops below 3:

```bash
kubectl rollout restart deployment/pingd & for i in $(seq 1 12); do kubectl get deployment pingd -o jsonpath='ready={.status.readyReplicas} updated={.status.updatedReplicas} total={.status.replicas}{"\n"}'; sleep 2; done
```

Now see the failure mode that readiness probes prevent. Deploy an image that does not exist:

```bash
kubectl set image deployment/pingd api=pingd:does-not-exist
```

```bash
sleep 15 && kubectl get pods -l app=pingd && kubectl rollout status deployment/pingd --timeout=20s
```

The old pods are still serving; the new one is stuck `ImagePullBackOff`; `rollout status`
times out non-zero. Nothing broke, because the rollout refused to proceed. Recover:

```bash
kubectl rollout undo deployment/pingd && kubectl rollout status deployment/pingd
```

## Takeaways

- Deployment → ReplicaSet → Pods. The indirection exists so old ReplicaSets can be retained
  at zero replicas, making rollback instant.
- The default strategy is `25%/25%`, which **goes below capacity during rollouts**. Use
  `maxSurge: 1, maxUnavailable: 0` in production.
- **Readiness probes gate rollouts.** Without one, a broken version rolls out completely.
- `progressDeadlineSeconds` stops a stalled rollout but does **not** roll back
  automatically.
- **`spec.selector` is immutable.** Never put a version in it. Overlapping selectors between
  Deployments cause pods to be destroyed continuously.
- `kubectl rollout undo` desynchronises the cluster from git — treat it as an emergency stop,
  then fix git.
- Remove `replicas` from manifests when an HPA owns it.
- `kubectl rollout restart` is the graceful way to restart everything.

---

Previous: [Chapter 5 — Pods](05-pods.md) ·
Next: [Chapter 7 — Configuration and secrets](07-config-and-secrets.md)

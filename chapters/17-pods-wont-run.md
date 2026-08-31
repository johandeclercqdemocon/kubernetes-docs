# Chapter 17 — Pods that won't run

Five failure states cover almost everything. Each has a distinctive signature, and each is
measured here against a real cluster.

## `Pending` — the scheduler could not place it

First, split the two cases:

```bash
kubectl get pod POD -o jsonpath='{.spec.nodeName}{"\n"}'
```

Empty → the scheduler could not find a node. Non-empty → it is scheduled and the kubelet is
still working (see `ContainerCreating` below).

For the unscheduled case, the event says exactly why:

```bash
kubectl describe pod POD | grep -A5 Events
```

Measured, for a pod requesting 500 CPUs and 900Gi:

```
Warning  FailedScheduling  8s  default-scheduler
  0/3 nodes are available: 1 node(s) had untolerated taint(s),
  2 Insufficient cpu, 2 Insufficient memory.
  preemption: 0/3 nodes are available: 3 Preemption is not helpful for scheduling.
```

Read it as arithmetic: 3 nodes, minus 1 for a taint, leaves 2, and both lacked capacity.

The common causes, in the order they appear in that message:

**`Insufficient cpu` / `Insufficient memory`.** Remember Chapter 8: this is about **requests**,
not usage. The node can be idle and still full.

```bash
kubectl describe node NODE | sed -n '/Allocated resources/,/Events/p'
```

Either lower the request or add capacity. A request larger than any single node's allocatable
can never schedule — check that first, because it is a manifest bug, not a capacity problem.

**`untolerated taint`.** Control-plane nodes are tainted by default; dedicated node pools
usually are too.

```bash
kubectl get nodes -o custom-columns='NAME:.metadata.name,TAINTS:.spec.taints[*].key'
```

Add a toleration, or target different nodes.

**`node(s) didn't match Pod's node affinity/selector`.** Your `nodeSelector` or affinity
matches nothing. Verify the labels actually exist:

```bash
kubectl get nodes --show-labels
```

**`pod has unbound immediate PersistentVolumeClaims`.** A PVC that will not bind (Chapter 15).
Note that with `WaitForFirstConsumer` a `Pending` PVC is *normal* and not the cause.

**`didn't match pod topology spread constraints`.** Chapter 25.

**No events at all** on a `Pending` pod means the scheduler never processed it — it is down,
or the pod names a non-existent `schedulerName`.

## `ContainerCreating` — scheduled, but not started

Scheduled (it has a `nodeName`) and stuck. The kubelet is doing something and failing.
`describe` tells you which:

- **Volume mount failures** — a PVC that will not attach, a node that cannot reach the storage
  backend, or a multi-attach error where an RWO volume is still mounted on another node. That
  last one is common after an ungraceful node failure and often needs manual intervention.
- **Image pull in progress** — normal for a large image on first pull; check the events for
  progress.
- **CNI failures** — `failed to setup network for sandbox`. The CNI plugin on that node is
  broken, or IP addresses are exhausted.
- **Secret/ConfigMap for a projected volume missing.**

Stuck here for more than a minute or two with no progress in the events is a node-level
problem (Chapter 21).

## `ImagePullBackOff` / `ErrImagePull`

Measured:

```bash
kubectl run f-pull --image=pingd:does-not-exist --restart=Never
```

```
STATUS    REASON
Pending   ErrImagePull
```

```
Normal   BackOff  10s  kubelet  Back-off pulling image "pingd:does-not-exist"
Warning  Failed   10s  kubelet  Error: ImagePullBackOff
```

`ErrImagePull` is the first failure; `ImagePullBackOff` is the retry backoff. The full reason
is in the events:

```bash
kubectl describe pod POD | grep -A10 Events
```

Causes, and how to tell them apart:

**Typo, or the tag does not exist.** Verify from outside the cluster:

```bash
docker manifest inspect IMAGE:TAG
```

**Private registry without credentials** — `pull access denied` or `authentication required`:

```yaml
spec:
  imagePullSecrets:
    - name: regcred
```

```bash
kubectl create secret docker-registry regcred \
  --docker-server=ghcr.io --docker-username=USER --docker-password="$TOKEN"
```

The Secret must be **in the same namespace as the pod**. Copying a working Deployment to a
new namespace and forgetting the pull secret is a very common cause.

**Rate limiting** — `toomanyrequests`. Authenticate to Docker Hub even for public images.

**Wrong architecture** — `no matching manifest for linux/arm64`. Chapter 10 of the Docker
book.

**The image exists locally but the cluster cannot see it.** This is the one that catches people
on local clusters: a `kind` or `minikube` node has its own image store. Your laptop's
`docker images` is irrelevant.

```bash
kind load docker-image pingd:latest --name k8sbook
```

And there is a subtlety that compounds it: **`imagePullPolicy` defaults to `Always` when the
tag is `:latest`**, and `IfNotPresent` otherwise. So a locally-loaded `:latest` image is still
pulled from the registry and fails. That is why this book's Deployment sets
`imagePullPolicy: IfNotPresent` explicitly — without it, the loaded image is ignored.

## `CreateContainerConfigError`

Measured, for a pod referencing a ConfigMap that does not exist:

```
STATUS    REASON                       MSG
Pending   CreateContainerConfigError   configmap "nope-missing" not found
```

The message is exact. Causes: a missing ConfigMap or Secret, a missing **key** within one, or
a name typo. Note this fails at *container creation*, not at `kubectl apply` — the Deployment
was accepted happily (Chapter 7).

Mark genuinely optional references as such:

```yaml
envFrom:
  - configMapRef:
      name: optional-config
      optional: true
```

The sibling error `CreateContainerError` usually means a bad command or working directory, and
`RunContainerError` a runtime-level failure.

## `CrashLoopBackOff` — and what it actually looked like

The container starts, exits, and the kubelet restarts it with exponential backoff.

Measured on this cluster with a container that exits 1 immediately, sampling every 2 seconds:

```
t=  2s  ContainerCreating restarts=0
t=  4s  Error             restarts=1
t= 18s  Error             restarts=2
t= 40s  Error             restarts=3
t= 86s  Error             restarts=4
```

Two things worth noting.

**The gaps are the backoff**: 14 s, 22 s, 46 s — roughly the documented 10/20/40-second
doubling, capped at 5 minutes. That progression *is* the diagnosis.

**The STATUS column said `Error` throughout, never `CrashLoopBackOff`.** On Kubernetes v1.37
with this failure shape, the string every tutorial describes did not appear. `CrashLoopBackOff`
is still extremely common and you will see it plenty — but do not treat its absence as
evidence that a container is not crash-looping. **The reliable signals are a climbing restart
count and widening gaps**, not a particular status string.

```bash
kubectl get pod POD -o jsonpath='restarts={.status.containerStatuses[0].restartCount} lastExit={.status.containerStatuses[0].lastState.terminated.exitCode}{"\n"}'
```

### Finding the actual error

The standard advice is `kubectl logs --previous`. It is right, and it does not always work:

```bash
kubectl logs f-crash --previous
```

```
unable to retrieve container logs for containerd://e62b046af8bb...
```

That happens when the previous container has already been cleaned up. In the same run, plain
`kubectl logs` **did** have the error, because the most recent attempt had run and exited:

```bash
kubectl logs f-crash
```

```
FATAL: cannot reach database
```

So: try both, and do not conclude there are no logs from one failing.

If both are empty, the container is dying before it writes anything. Then:

**Check the exit code.** 137 = SIGKILL (OOM or an ignored SIGTERM — Chapter 8). 1 = the
application chose to exit. 127 = command not found. 126 = not executable. All the Docker
book's exit codes apply unchanged.

**Replace the command with a sleep** to hold the environment open and look around:

```bash
kubectl run probe --image=pingd:latest --restart=Never --command -- sleep 3600
```

```bash
kubectl exec -it probe -- sh
```

**Or use `kubectl debug` to copy the pod with a different command** (Chapter 18):

```bash
kubectl debug POD --copy-to=probe --container=api -- sleep 3600
```

### Common causes

- **A missing required environment variable**, and the app fails fast (which is correct —
  Chapter 7).
- **A dependency unreachable at startup.** Add retries; `initContainers` help at startup only.
- **A liveness probe failing** — check `restartCount` against probe events. A container
  restarted by liveness looks identical to one that crashed, except the events say
  `Unhealthy`.
- **Wrong command or entrypoint**, especially after switching to a distroless base.
- **Permission denied** on a mounted volume, because the image runs as non-root and `fsGroup`
  was not set (Chapter 15).
- **OOM on startup** — a JVM sizing its heap from the node rather than the limit (Chapter 8).

## `Terminating` forever

A pod stuck `Terminating` is usually one of:

**A long grace period plus a process ignoring SIGTERM.** It will resolve at
`terminationGracePeriodSeconds`. If it always takes exactly that long, PID 1 is not handling
signals (Chapter 9).

**A finalizer.** Something registered cleanup that has not completed:

```bash
kubectl get pod POD -o jsonpath='{.metadata.finalizers}{"\n"}'
```

**A gone node.** Pods on a `NotReady` node stay `Terminating` because the kubelet that should
confirm deletion is unreachable (Chapter 21).

Force deletion removes the API object without confirming the container stopped:

```bash
kubectl delete pod POD --force --grace-period=0
```

**⚠️ Use this carefully.** For a StatefulSet with attached storage, the container may still be
running on an unreachable node, and forcing deletion lets a replacement start — two writers on
one volume. That is how you corrupt a database.

## Try it

Reproduce each state and read its signature.

```bash
kubectl run f-pull --image=pingd:does-not-exist --restart=Never
```

```bash
sleep 12 && kubectl get pod f-pull && kubectl describe pod f-pull | grep -A5 Events
```

```bash
kubectl run f-cfg --image=busybox:1.37 --restart=Never --overrides='{"spec":{"containers":[{"name":"f-cfg","image":"busybox:1.37","command":["sleep","300"],"envFrom":[{"configMapRef":{"name":"nope-missing"}}]}]}}'
```

```bash
sleep 10 && kubectl get pod f-cfg -o custom-columns='STATUS:.status.phase,REASON:.status.containerStatuses[0].state.waiting.reason,MSG:.status.containerStatuses[0].state.waiting.message'
```

Now a crash loop, sampled so you can see the backoff widening:

```bash
kubectl run f-crash --image=busybox:1.37 --restart=Always --command -- sh -c 'echo "FATAL: cannot reach database" >&2; exit 1'
```

```bash
for i in $(seq 1 30); do kubectl get pod f-crash --no-headers; sleep 4; done
```

Watch the restart count climb with widening gaps. Then find the error:

```bash
kubectl logs f-crash; kubectl logs f-crash --previous
```

And an unschedulable pod, for the arithmetic in the event message:

```bash
kubectl run f-pend --image=busybox:1.37 --restart=Never --overrides='{"spec":{"containers":[{"name":"f-pend","image":"busybox:1.37","command":["sleep","300"],"resources":{"requests":{"cpu":"500","memory":"900Gi"}}}]}}'
```

```bash
sleep 8 && kubectl describe pod f-pend | grep -A4 Events
```

Clean up:

```bash
kubectl delete pod f-pull f-cfg f-crash f-pend --force --grace-period=0
```

## Takeaways

- `Pending` with no `nodeName` is the scheduler; with a `nodeName` it is the kubelet. Check
  which before anything else.
- `FailedScheduling` messages are arithmetic — read them literally.
- Scheduling failures are about **requests**, not usage. A request bigger than any node is a
  manifest bug.
- `imagePullPolicy` defaults to `Always` for `:latest`, which is why locally-loaded images are
  ignored on kind/minikube. Set `IfNotPresent`.
- `imagePullSecrets` must be in the pod's namespace.
- `CreateContainerConfigError` names the missing ConfigMap or Secret exactly, and happens at
  container creation, not at apply.
- **Measured on v1.37, a crash loop showed `Error`, not `CrashLoopBackOff`.** Trust the
  climbing restart count and widening gaps, not the status string.
- `kubectl logs --previous` can fail with "unable to retrieve"; try plain `logs` too.
- To debug a crash loop, replace the command with `sleep` or use `kubectl debug --copy-to`.
- **⚠️ Force-deleting a StatefulSet pod on an unreachable node risks two writers on one
  volume.**

---

Previous: [Chapter 16 — The debugging mindset](16-debugging-mindset.md) ·
Next: [Chapter 18 — Getting inside](18-getting-inside.md)

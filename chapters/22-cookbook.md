# Chapter 22 — Cookbook: symptom → cause → fix

The reference chapter. Grouped by what you actually see, most likely cause first.

---

## Pods

### `Pending`, no node assigned

```bash
kubectl describe pod POD | grep -A5 Events
```

`FailedScheduling` messages are arithmetic — read them literally:

| Message fragment | Cause | Fix |
|---|---|---|
| `Insufficient cpu` / `memory` | **Requests** exceed free allocatable (not usage) | Lower requests, add nodes |
| `untolerated taint` | Node tainted | Add toleration, or target other nodes |
| `didn't match node affinity/selector` | Labels do not exist | `kubectl get nodes --show-labels` |
| `unbound immediate PersistentVolumeClaims` | PVC will not bind | Ch 15 |
| `didn't match pod topology spread constraints` | Spread rules unsatisfiable | Ch 25 |

**No events at all** → the scheduler is down, or `schedulerName` names something nonexistent.

A request larger than any single node's allocatable can never schedule. Check that first — it
is a manifest bug, not a capacity problem.

---

### `Pending` **with** a node assigned

Not a scheduler problem. The kubelet is stuck on image pull, volume mount, or init containers.
`describe` says which. Stuck >2 minutes with no event progress → node problem (Ch 21).

---

### `ImagePullBackOff` / `ErrImagePull`

```bash
kubectl describe pod POD | grep -A10 Events
```

| Message | Fix |
|---|---|
| `not found` / `manifest unknown` | Typo or missing tag. Verify with `docker manifest inspect` |
| `pull access denied` / `authentication required` | `imagePullSecrets` — **must be in the pod's namespace** |
| `toomanyrequests` | Authenticate to Docker Hub, or mirror |
| `no matching manifest for linux/amd64` | Wrong architecture |

**Local image on kind/minikube not being used?** `imagePullPolicy` defaults to `Always` for
`:latest`. Load the image *and* set `imagePullPolicy: IfNotPresent`.

```bash
kind load docker-image IMAGE:TAG --name CLUSTER
```

---

### `CreateContainerConfigError`

```bash
kubectl get pod POD -o jsonpath='{.status.containerStatuses[0].state.waiting.message}{"\n"}'
```

```
configmap "nope-missing" not found
```

Missing ConfigMap/Secret, or a missing key within one. Note it fails at container creation,
not at `apply`. Use `optional: true` if genuinely optional.

---

### `CrashLoopBackOff` — or `Error` with a climbing restart count

**Measured on v1.37, the STATUS column showed `Error`, never `CrashLoopBackOff`.** Do not rely
on the string. The reliable signals:

```bash
kubectl get pod POD -o jsonpath='restarts={.status.containerStatuses[0].restartCount} lastExit={.status.containerStatuses[0].lastState.terminated.exitCode}{"\n"}'
```

A climbing restart count with widening gaps (≈10s, 20s, 40s, capped at 5 min) is the backoff.

Find the error — **try both**, `--previous` can fail with "unable to retrieve container logs":

```bash
kubectl logs POD --previous; kubectl logs POD
```

Exit codes: `1` app error · `126` not executable · `127` not found · `137` SIGKILL (OOM or
ignored SIGTERM) · `143` SIGTERM.

Both logs empty → hold the environment open:

```bash
kubectl debug POD --copy-to=probe --container=CONTAINER -- sleep 3600
```

Causes: missing required env var; unreachable dependency at startup; **liveness probe failing**
(events say `Unhealthy`); wrong command after a base-image change; permission denied on a
volume (`fsGroup`); OOM at startup.

---

### `Running` but `0/1` READY

Readiness failing. Not a networking problem.

```bash
kubectl describe pod POD | grep -A5 Events    # look for Unhealthy
```

Check the probe uses **`127.0.0.1`, not `localhost`** (which is `::1`), a path that exists, and
`timeoutSeconds` above the default 1 s.

---

### `Terminating` forever

| Cause | Check |
|---|---|
| Long grace period + ignored SIGTERM | Always exactly `terminationGracePeriodSeconds`? PID 1 issue |
| Finalizer | `kubectl get pod POD -o jsonpath='{.metadata.finalizers}'` |
| Node gone | `kubectl get nodes` |

```bash
kubectl delete pod POD --force --grace-period=0
```

**⚠️ For StatefulSet pods on an unreachable node this risks two writers on one volume.**

---

### `Evicted`

```bash
kubectl describe pod POD | grep -i message
```

`The node was low on resource: ephemeral-storage` (most common) or `memory`. BestEffort pods
go first — **set requests** (Ch 8, 20).

---

### Exit 137

```bash
kubectl get pod POD -o jsonpath='{.status.containerStatuses[0].lastState.terminated}{"\n"}'
```

`reason: OOMKilled` confirms memory — but its **absence does not rule it out** (measured
`Error` here). Confirm on the node:

```bash
kubectl debug node/NODE -it --image=busybox:1.37 -- chroot /host dmesg -T | grep -i 'memory cgroup out of memory'
```

137 during a delete/rollout with memory well below the limit → ignored SIGTERM instead.

---

## Networking

### Service unreachable — check this first

```bash
kubectl get endpointslice -l kubernetes.io/service-name=SVC \
  -o custom-columns='ADDRESSES:.endpoints[*].addresses[0],READY:.endpoints[*].conditions.ready'
```

| Result | Cause |
|---|---|
| `<unset>` / empty | **Selector does not match pod labels** — the most common bug |
| Addresses, `ready: false` | Readiness failing (Ch 9) |
| Addresses, ready | Service is fine — look elsewhere |

```bash
kubectl get svc SVC -o jsonpath='{.spec.selector}{"\n"}'; kubectl get pods --show-labels
```

Also verify `targetPort` matches the container's real port — a mismatch gives a healthy
Service that refuses everything.

---

### Name does not resolve

- Bare names only resolve **in the pod's own namespace**. Use `svc.namespace`.
- `hostNetwork: true` needs `dnsPolicy: ClusterFirstWithHostNet`, or no Service resolves.
- CoreDNS down: `kubectl get endpointslice -n kube-system -l kubernetes.io/service-name=kube-dns`

---

### External DNS is slow

`ndots:5` costs 4 queries per external name (3 NXDOMAIN). Fix with a trailing dot
(`api.example.com.`), `ndots: 2` via `dnsConfig`, or NodeLocal DNSCache. (Ch 12)

---

### Intermittent ~5-second DNS failures

The A/AAAA conntrack race. Use `single-request-reopen`, NodeLocal DNSCache, or a different
CNI.

---

### Connection refused

App bound to `127.0.0.1` inside the container, or `localhost` → `::1` against an IPv4-only
listener.

```bash
kubectl debug POD --image=nicolaka/netshoot -it -- ss -tln
```

Want `0.0.0.0:PORT`, not `127.0.0.1:PORT`.

---

### Connection times out

NetworkPolicy (Ch 14), CNI, or wrong address.

```bash
kubectl get networkpolicy -A
```

Policy traps: uses the **Service** port instead of the **pod** port; egress policy forgot
DNS; `namespaceSelector`+`podSelector` in separate list items (OR) instead of the same item
(AND).

**Verify your CNI enforces policy at all** — Flannel does not.

---

### Works from some pods, not others

kube-proxy broken on the client's node → that node cannot reach any ClusterIP but *can* reach
pod IPs.

```bash
kubectl get pods -n kube-system -l k8s-app=kube-proxy -o wide
```

---

### Ingress 404 / 503 / connection reset

| Symptom | Cause |
|---|---|
| 404 | No rule matched — `Host` header, or `pathType: Prefix` is **segment-based** |
| 503 | Backend Service has no ready endpoints |
| Connection reset | **Controller pod is not where external traffic arrives** |
| Nothing happens at all | No controller — `kubectl get ingressclass` |

A misspelled ingress annotation is **silently ignored**. No error, no event.

---

## Configuration

### ConfigMap change had no effect

**Env vars freeze at container start.** Volume-mounted files update in ~1 minute;
**`subPath` mounts never update.**

```bash
kubectl rollout restart deployment/NAME
```

Better: a config checksum annotation on the pod template (Ch 7).

---

### Secret value is wrong

```bash
kubectl get secret NAME -o jsonpath='{.data.KEY}' | base64 -d
```

`stringData` takes plaintext; `data` requires you to encode. Mixing them up is common.

---

## Storage

### PVC `Pending`

With `WaitForFirstConsumer` this is **normal** until a pod uses it.

```bash
kubectl describe pvc NAME | grep -A3 Events
```

Otherwise: no default StorageClass, an access mode the backend cannot provide (RWX on block
storage), or `storageClassName: ""` (explicitly no class → static binding only).

---

### Pod stuck `ContainerCreating` with a volume error

`Multi-Attach error` → an RWO volume still attached to another node, typically after an
ungraceful node failure. Wait for the attach to time out, or detach manually.

---

### Data disappeared after deleting a PVC

`reclaimPolicy: Delete` on most default StorageClasses destroys the volume. Use a `Retain`
class for anything that matters — and have backups.

---

## Cluster

### Node `NotReady`

```bash
kubectl debug node/NODE -it --image=nicolaka/netshoot -- chroot /host bash
systemctl status kubelet; journalctl -u kubelet --since '15 min ago' | tail -50; df -h
```

Timeline: `NotReady` at ~40 s, pods rescheduled at ~5 min. **In between, containers keep
running on the node.**

---

### `kubectl` hangs or auth fails

```bash
kubectl get --raw='/readyz?verbose'
```

```
Unable to connect: x509: certificate has expired
```

→ `kubeadm certs check-expiration` / `kubeadm certs renew all`. kubeadm certs expire after
**one year**.

Wrong context is the other classic: `kubectl config current-context`.

---

### Everything is slow

etcd disk latency. Watch `etcd_disk_wal_fsync_duration_seconds`. A slow disk presents as
cluster-wide API slowness with no obvious culprit.

---

### All writes rejected

etcd exceeded its 2 GiB quota and entered maintenance mode. Defragment, raise the quota, and
find what is creating objects.

---

### Drain hangs

A PodDisruptionBudget is blocking it — which is the PDB working.

```bash
kubectl get pdb -A
```

A PDB with `minAvailable` equal to replica count blocks drains **forever**.

---

### Pods did not move back after uncordon

They never do. **The scheduler does not rebalance.**

```bash
kubectl rollout restart deployment/NAME
```

---

### Namespace stuck `Terminating`

```bash
kubectl get namespace NS -o jsonpath='{.status.conditions}' | python3 -m json.tool
kubectl get apiservice | grep -v True
```

A broken APIService blocks namespace deletion cluster-wide. **Stripping finalizers leaks real
resources** — last resort.

---

### Upgrade broke manifests

Removed API versions.

```bash
kubectl get --raw /metrics | grep apiserver_requested_deprecated_apis
```

Run `pluto` or `kubent` before every upgrade. Never skip minor versions.

---

## Deployments

### Rollout stuck

```bash
kubectl rollout status deployment/NAME --timeout=60s
kubectl get pods -l app=NAME
```

New pods not becoming ready → readiness probe or a broken image. **This is readiness doing its
job** — with `maxUnavailable: 0` the old pods keep serving.

`progressDeadlineSeconds` stops the rollout but does **not** roll back automatically.

```bash
kubectl rollout undo deployment/NAME
```

---

### Change had no effect

```bash
kubectl get deployment NAME -o jsonpath='gen={.metadata.generation} observed={.status.observedGeneration}{"\n"}'
```

Differ → no controller processed it. Equal → it was applied; check you edited the right
object, namespace, or cluster.

---

### `replicas` keeps changing back

An HPA (or GitOps controller) owns the field. **Remove `replicas` from your manifest.**

---

### Cannot change the selector

```
spec.selector: Invalid value: ...: field is immutable
```

Delete and recreate the Deployment. Never put a version in a selector.

---

### Pods created and destroyed continuously

Two Deployments with **overlapping selectors** adopting each other's pods. Selectors must be
disjoint.

---

## Takeaways

- Recurring root causes across this whole chapter are few: **selector mismatch**, missing
  resource requests, probes using `localhost`, env-var config that never refreshes,
  `imagePullPolicy: Always` on `:latest`, and requests-vs-usage confusion.
- Two status strings are unreliable on current versions: `CrashLoopBackOff` (may show `Error`)
  and `OOMKilled` (may show `Error`). Use restart counts and exit code 137.
- When nothing here matches, go back to Chapter 16 and localise the layer.

---

Previous: [Chapter 21 — Nodes and the control plane](21-nodes-and-control-plane.md) ·
Next: [Chapter 23 — Security](23-security.md)

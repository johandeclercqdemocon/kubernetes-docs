# Chapter 20 — Resources and eviction

Chapter 8 covered how requests and limits are declared. This chapter covers what happens when
they are wrong: containers killed for memory, throttled for CPU, and pods evicted by a node
under pressure.

## OOMKilled

A container exceeding `limits.memory` is killed by the kernel's cgroup OOM killer.

The measurement from Chapter 8 is worth restating here because it changes the procedure. On
this cluster (v1.37, containerd 2.3.4, cgroup v2), a container that was definitively
OOM-killed reported:

```json
{"terminated":{"exitCode":137,"reason":"Error", ...}}
```

`reason: Error`, **not** `OOMKilled` — while the node's kernel log was unambiguous:

```
Memory cgroup out of memory: Killed process 160986 (python)
```

`OOMKilled` does appear on many clusters, so treat its absence as inconclusive rather than
exculpatory. The reliable procedure:

**1. Exit code 137.** Always present.

```bash
kubectl get pod POD -o jsonpath='exit={.status.containerStatuses[0].lastState.terminated.exitCode} reason={.status.containerStatuses[0].lastState.terminated.reason}{"\n"}'
```

**2. Was it at its limit?**

```bash
kubectl top pod POD --containers
```

**3. The node's kernel log** — definitive:

```bash
kubectl debug node/NODE -it --image=busybox:1.37 -- chroot /host dmesg -T | grep -i 'memory cgroup out of memory' | tail
```

**4. Distinguish from an ignored SIGTERM.** 137 is SIGKILL, which is also what you get when a
pod exceeds its termination grace period (Chapter 9). If the kill happened during a delete or
a rollout, and memory was nowhere near the limit, it is a signal-handling problem, not memory.

### Fixing it

- **Raise the limit** if the workload legitimately needs it.
- **Fix the runtime's sizing.** A JVM using `-Xmx` equal to the container limit will always
  eventually OOM, because heap is not the JVM's only memory. Use `-XX:MaxRAMPercentage=75`.
  Node needs `--max-old-space-size` explicitly. Pass the real limit in with the downward API
  (Chapter 7).
- **Look for a leak.** Memory climbing steadily to the limit, then a restart, then climbing
  again, is a leak with a restart schedule.
- **Check `initialDelaySeconds` on liveness** if the OOM happens at startup — some runtimes
  peak during initialisation.

Note that a container hitting its memory limit is killed *individually*. The pod is not
rescheduled; the container restarts in place with the same pod IP.

## CPU throttling

CPU limits do not kill; they throttle. The symptom is latency at low reported utilisation,
and it is one of the most misdiagnosed problems in Kubernetes.

The mechanism is exactly what the Docker book measured: `limits.cpu: 500m` is a quota of 50 ms
per 100 ms period. Multi-threaded processes exhaust it early and then freeze for the rest of
the period.

The metric to look at:

```
container_cpu_cfs_throttled_periods_total / container_cpu_cfs_periods_total
```

Anything above a few percent on a latency-sensitive service is worth attention. `kubectl top`
will **not** show this — throttling keeps average utilisation low, which is why the problem
hides.

Without Prometheus, read the cgroup directly via a node debug pod:

```bash
kubectl debug node/NODE -it --image=busybox:1.37 -- \
  chroot /host sh -c 'find /sys/fs/cgroup/kubelet.slice -name cpu.stat | head -3 | xargs grep -H nr_throttled'
```

Fixes: raise the limit, reduce worker/thread counts to match the quota (do not size pools from
`nproc` — it reports the *node's* cores), or remove the CPU limit and rely on requests for
fair sharing.

For latency-critical workloads — the SIP load generator being the case in point — CPU limits
are actively harmful. Use `Guaranteed` QoS with the static CPU manager policy for exclusive
cores instead.

## Node pressure and eviction

When a node runs short of memory, disk or PIDs, the **kubelet** evicts pods to recover. This
is distinct from OOM killing: the kubelet chooses victims deliberately, and the pod is
*deleted* and rescheduled elsewhere rather than restarted in place.

```bash
kubectl get nodes -o custom-columns='NAME:.metadata.name,MEM:.status.conditions[?(@.type=="MemoryPressure")].status,DISK:.status.conditions[?(@.type=="DiskPressure")].status,PID:.status.conditions[?(@.type=="PIDPressure")].status'
```

```
NAME                    MEM     DISK    PID
k8sbook-control-plane   False   False   False
k8sbook-worker          False   False   False
k8sbook-worker2         False   False   False
```

Any of these `True` means the node is shedding load.

### Eviction order

The kubelet ranks candidates by:

1. **QoS class** — `BestEffort` first, then `Burstable`, then `Guaranteed`.
2. **Usage above requests** — a pod far over its request is evicted before one within it.
3. **Pod priority** — lower `priorityClassName` first.

This is the practical consequence of Chapter 8: a pod with no resources block is `BestEffort`
and is evicted first, every time. Setting requests is not bureaucracy; it is survival ranking.

An evicted pod:

```bash
kubectl get pods --field-selector status.phase=Failed
```

```bash
kubectl describe pod POD | grep -A3 'Status:\|Message:'
```

The message names the resource: `The node was low on resource: memory` or
`ephemeral-storage`.

### Thresholds

The kubelet's thresholds are configuration, not universal constants. On this cluster:

```bash
kubectl get --raw /api/v1/nodes/k8sbook-worker/proxy/configz | jq .kubeletconfig.evictionHard
```

```json
{"imagefs.available":"0%","nodefs.available":"0%","nodefs.inodesFree":"0%"}
```

**kind sets these to 0%, effectively disabling eviction** — sensible for a local test cluster,
and a genuine difference from production, where the defaults are around
`memory.available<100Mi`, `nodefs.available<10%`, `imagefs.available<15%`. So eviction is one
of the few behaviours you cannot exercise realistically on kind. Check your real clusters
rather than assuming defaults.

Similarly, `capacity` and `allocatable` are identical here:

```
capacity:    cpu=22 mem=32448976Ki pods=110
allocatable: cpu=22 mem=32448976Ki pods=110
```

Production nodes reserve capacity for the kubelet and the OS (`--kube-reserved`,
`--system-reserved`), so allocatable is meaningfully lower than capacity. **Always schedule
against allocatable**, and be aware that a managed node pool's usable capacity is 5–15% below
the instance size you are paying for.

### Ephemeral storage

The most common eviction cause in practice, and the least anticipated. Container logs,
writable layers and `emptyDir` volumes all consume node disk. One pod writing a large log or
filling an `emptyDir` triggers `DiskPressure`, and the kubelet evicts pods — **including
well-behaved ones** on the same node.

Defend with requests and limits (Chapter 8), `sizeLimit` on `emptyDir`, and log rotation.

## Preemption

Distinct from eviction. When a high-priority pod cannot schedule, the scheduler may **evict
lower-priority pods** to make room.

```bash
kubectl get priorityclass
```

```
system-cluster-critical   2000000000
system-node-critical      2000001000
```

Those two ship with every cluster and are reserved for control-plane components. Define your
own for workload tiers:

```yaml
apiVersion: scheduling.k8s.io/v1
kind: PriorityClass
metadata:
  name: high-priority
value: 1000000
globalDefault: false
preemptionPolicy: PreemptLowerPriority
description: "Customer-facing services"
```

Use `preemptionPolicy: Never` for pods that should get scheduling *preference* without
displacing anything.

Preemption shows in the scheduler's message, as seen in Chapter 17:

```
preemption: 0/3 nodes are available: 3 Preemption is not helpful for scheduling.
```

That line means the scheduler considered preemption and concluded it would not help — usually
because the pending pod's request is larger than anything it could free.

A caution: priority is cluster-wide and comparative. Once teams start assigning priorities,
they all assign high ones. Govern priority classes centrally, or the mechanism stops
discriminating.

## Disruption budgets

Eviction also happens *voluntarily* — a node drain for maintenance or an autoscaler
scale-down. A PodDisruptionBudget limits how much of a workload may be voluntarily disrupted
at once:

```yaml
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: pingd
spec:
  minAvailable: 2
  selector:
    matchLabels:
      app: pingd
```

`kubectl drain` respects this and blocks rather than taking your service below two replicas.
Note the asymmetry: **PDBs do not protect against involuntary disruption** — node failure,
kernel OOM, or kubelet eviction under pressure ignore them entirely. Chapter 25 covers them
properly.

A PDB with `minAvailable` equal to the replica count blocks drains **forever**, which turns a
routine node upgrade into an incident. `maxUnavailable: 1` is usually safer.

## Try it

Look at the pressure conditions and allocatable capacity:

```bash
kubectl get nodes -o custom-columns='NAME:.metadata.name,MEM:.status.conditions[?(@.type=="MemoryPressure")].status,DISK:.status.conditions[?(@.type=="DiskPressure")].status'
```

```bash
kubectl get node k8sbook-worker -o jsonpath='capacity={.status.capacity.cpu}/{.status.capacity.memory}{"\n"}allocatable={.status.allocatable.cpu}/{.status.allocatable.memory}{"\n"}'
```

Check your kubelet's real eviction thresholds rather than assuming:

```bash
kubectl get --raw /api/v1/nodes/k8sbook-worker/proxy/configz | python3 -c "import json,sys; print(json.load(sys.stdin)['kubeletconfig'].get('evictionHard'))"
```

Trigger an OOM kill and read every signal:

```bash
kubectl apply -f examples/manifests/08-oom.yaml && sleep 40
```

```bash
kubectl get pod oom3 -o jsonpath='exit={.status.containerStatuses[0].lastState.terminated.exitCode} reason={.status.containerStatuses[0].lastState.terminated.reason} restarts={.status.containerStatuses[0].restartCount}{"\n"}'
```

Then confirm from the node that it really was memory:

```bash
kubectl debug node/$(kubectl get pod oom3 -o jsonpath='{.spec.nodeName}') -it --image=busybox:1.37 --quiet -- chroot /host sh -c "dmesg -T | grep -i 'memory cgroup out of memory' | tail -2"
```

```bash
kubectl delete -f examples/manifests/08-oom.yaml --force --grace-period=0
```

Create a PriorityClass and see it applied:

```bash
kubectl apply -f examples/manifests/20-priorityclass.yaml && kubectl get priorityclass high-priority
```

```bash
kubectl delete -f examples/manifests/20-priorityclass.yaml
```

Clean up the node debugger pods this created:

```bash
kubectl get pods -o name | grep node-debugger | xargs -r kubectl delete --force --grace-period=0
```

## Takeaways

- **Exit 137 is the reliable OOM signal**, not `reason: OOMKilled` — measured `Error` here.
  Confirm with the node's `Memory cgroup out of memory` log.
- 137 during a delete or rollout, with memory nowhere near the limit, is an ignored SIGTERM
  instead.
- CPU throttling causes latency at *low* reported utilisation. `kubectl top` cannot show it;
  use `container_cpu_cfs_throttled_periods_total` or read `cpu.stat` on the node.
- Eviction ranks by **QoS class, then usage above requests, then priority**. BestEffort pods
  go first — which is what "forgetting to set resources" actually costs.
- **kind disables eviction** (`evictionHard: 0%`) and reserves nothing, so allocatable equals
  capacity. Production differs on both counts; check your real clusters.
- Ephemeral storage is the most common eviction trigger, and it evicts innocent neighbours.
- Preemption is the scheduler displacing lower-priority pods; eviction is the kubelet shedding
  load. Different mechanisms, different fixes.
- PDBs constrain **voluntary** disruption only, and a too-strict PDB blocks node drains
  forever.

---

Previous: [Chapter 19 — Network diagnosis](19-debugging-networks.md) ·
Next: [Chapter 21 — Nodes and the control plane](21-nodes-and-control-plane.md)

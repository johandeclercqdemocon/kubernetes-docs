# Chapter 8 — Resources, requests and QoS

Two numbers per container decide where it runs, how it behaves under pressure, and whether
it survives a node running short of memory. They are the most consequential fields in a pod
spec and the most commonly left out.

```yaml
resources:
  requests:
    cpu: 50m
    memory: 96Mi
  limits:
    cpu: 500m
    memory: 192Mi
```

## Requests and limits are different things

**Requests are for the scheduler.** "This container needs at least this much." The scheduler
sums the requests of all pods on a node and will not place a pod whose requests do not fit
in what remains. Requests are also the basis of QoS and eviction ordering.

**Limits are for the kernel.** "This container may not exceed this." Enforced by cgroups on
the node, exactly as in the Docker book.

The critical consequence, which causes more confusion than anything else in this chapter:

> **Scheduling is based on requests, never on actual usage.**

```bash
kubectl describe node k8sbook-worker | sed -n '/Allocated resources/,/Events/p'
```

```
Allocated resources:
  (Total limits may be over 100 percent, i.e., overcommitted.)
  Resource   Requests    Limits
  --------   --------    ------
  cpu        200m (0%)   700m (3%)
  memory     178Mi (0%)  320Mi (1%)
```

Those percentages are of *allocatable*, and they are what the scheduler sees. A node whose
pods have requested everything will refuse new pods even if it is completely idle. A node
running at 95% CPU will happily accept more pods if those pods requested little. Neither is
a bug; both surprise people.

Note the parenthetical the node itself prints: limits may exceed 100%. The cluster is
deliberately overcommitted, which is the whole point of separating the two numbers.

## CPU

CPU is measured in cores; `m` means millicores, so `500m` is half a core. It is a
**compressible** resource: exceeding the limit does not kill you, it throttles you.

The mechanism is exactly what the Docker book measured — a cgroup bandwidth quota, not a
slower core. `limits.cpu: 500m` becomes a quota of 50 ms per 100 ms period. A multi-threaded
process burns that in a few milliseconds and then freezes for the rest of the period,
producing latency cliffs while average CPU utilisation looks modest.

`requests.cpu` translates into a **CPU share weight**, which only matters under contention.
Under contention, a pod requesting 500m gets roughly five times the CPU of one requesting
100m.

The recurring argument about whether to set CPU limits at all is worth stating fairly:

**For CPU limits:** predictable behaviour, no noisy neighbours, and workloads cannot come to
depend on burst capacity that will not exist when the node is busy.

**Against CPU limits:** throttling causes latency even when the node is idle, which is pure
waste. Requests alone already provide fair sharing under contention.

The defensible position for most latency-sensitive services is **always set requests; set
CPU limits only where you need hard isolation or predictable capacity planning**. For batch
work, limits are fine. For the SIP load-generation case, CPU limits are actively harmful —
RTP pacing is destroyed by 90 ms throttle stalls, and `nr_throttled` is the first thing to
check when jitter measurements look wrong.

Always set **memory** limits, though. Memory is not compressible.

## Memory

Memory is **incompressible**: you cannot throttle a process's memory, you can only kill it.
Exceed `limits.memory` and the kernel OOM-kills the container.

### What an OOM kill actually looks like

This is where measurement contradicted the standard advice, so it needs care.

A container with `limits.memory: 128Mi` that allocates until it dies:

```bash
kubectl get pod oom3 -o jsonpath='{.status.containerStatuses[0].lastState}'
```

```json
{"terminated":{"exitCode":137,"reason":"Error","startedAt":"...","finishedAt":"..."}}
```

**`reason: Error`, not `reason: OOMKilled`.** And yet the node's kernel log is unambiguous:

```bash
docker exec k8sbook-worker dmesg -T | grep -i 'killed process'
```

```
[Mon Aug 31 12:27:39 2026] Memory cgroup out of memory: Killed process 160986 (python)
  total-vm:157584kB, anon-rss:130100kB, ... oom_score_adj:996
```

It was definitively OOM-killed by the memory cgroup, and the pod status did not say so.
Repeated across restarts, the result was the same every time.

This appears to be the cgroup v2 `memory.oom.group` path: the whole container cgroup is
killed at once, and on this stack (Kubernetes v1.37, containerd 2.3.4, cgroup v2) it surfaces
as a generic `Error`. `OOMKilled` **does** still appear in many environments — plenty of
clusters report it correctly — so treat this as "the absence of `OOMKilled` does not rule out
an OOM kill", not as "the field is gone".

The reliable diagnosis, in order:

1. **Exit code 137.** Always present for a SIGKILL. This is the primary signal.
2. **Memory usage sitting at the limit** before death (`kubectl top pod`, or metrics).
3. **Node kernel logs** — `Memory cgroup out of memory` names the cgroup and the process.
4. `reason: OOMKilled` when your stack reports it, which is a confirmation rather than a
   requirement.

Chapter 20 builds the full procedure. The practical takeaway for now: **do not conclude
"not an OOM" just because the reason says `Error`.**

### Sizing memory

Set `requests.memory` to a realistic steady-state figure and `limits.memory` with headroom
for peaks. Runtimes that size themselves from the host rather than the cgroup need telling —
the downward API (Chapter 7) is the clean way:

```yaml
env:
  - name: MEMORY_LIMIT
    valueFrom:
      resourceFieldRef:
        containerName: api
        resource: limits.memory
```

Modern JVMs read cgroup limits themselves; use `-XX:MaxRAMPercentage=75` rather than a fixed
`-Xmx` so it tracks the limit. Node needs `--max-old-space-size` set explicitly. Python has
no heap limit and will allocate until killed.

## QoS classes

Kubernetes derives a QoS class from your requests and limits. You do not set it; it is
computed. Measured:

```bash
kubectl get pods -o custom-columns='NAME:.metadata.name,QOS:.status.qosClass'
```

```
qos-guaranteed   Guaranteed
qos-burstable    Burstable
qos-besteffort   BestEffort
```

| Class | Condition | Eviction order |
|---|---|---|
| **Guaranteed** | Every container has requests **equal to** limits, for both CPU and memory | Last |
| **Burstable** | At least one request set, but not equal to limits | Middle |
| **BestEffort** | No requests or limits at all | **First** |

When a node runs short of memory, the kubelet evicts BestEffort pods first, then Burstable
pods exceeding their requests, and Guaranteed pods last. Chapter 20 covers eviction in
detail.

**BestEffort is the class you get by forgetting.** A pod with no resources block is first
against the wall on a busy node, and it is also invisible to the scheduler's accounting,
which means it contributes to overcommit without being counted. Set requests on everything.

`Guaranteed` additionally unlocks exclusive CPU pinning via the static CPU manager policy,
which matters for latency-sensitive workloads — the Kubernetes equivalent of the Docker
book's `--cpuset-cpus` advice.

## Other resources

**Ephemeral storage** — the container's writable layer, `emptyDir` volumes and logs. Worth
limiting, because a pod that fills the node's disk causes `DiskPressure` and evicts
*everything else*:

```yaml
resources:
  requests: { ephemeral-storage: 1Gi }
  limits:   { ephemeral-storage: 2Gi }
```

**Extended resources** — GPUs and similar, advertised by device plugins. Only `limits` is
meaningful; requests are set equal automatically, and they are not overcommittable:

```yaml
resources:
  limits:
    nvidia.com/gpu: 1
```

## Defaults and governance

Two namespace-scoped objects make good behaviour the default (Chapter 28):

**LimitRange** supplies defaults to pods that specify nothing, and can enforce minimums and
maximums:

```yaml
apiVersion: v1
kind: LimitRange
metadata:
  name: defaults
spec:
  limits:
    - type: Container
      default:        { cpu: 500m, memory: 256Mi }
      defaultRequest: { cpu: 50m,  memory: 64Mi }
      max:            { cpu: "4",  memory: 4Gi }
```

This is the cheapest way to eliminate BestEffort pods across a namespace.

**ResourceQuota** caps total consumption per namespace, and can require that requests and
limits are set at all:

```yaml
apiVersion: v1
kind: ResourceQuota
metadata:
  name: team-quota
spec:
  hard:
    requests.cpu: "10"
    requests.memory: 20Gi
    limits.cpu: "20"
    limits.memory: 40Gi
    pods: "50"
```

Note the interaction that catches people: **once a ResourceQuota on `requests.cpu` exists in
a namespace, every pod in it must specify that request** or creation is rejected. Combine
with a LimitRange so existing manifests keep working.

## Sizing in practice

Measure rather than guess:

```bash
kubectl top pods --containers
```

```bash
kubectl top nodes
```

(`kubectl top` needs metrics-server; Chapter 26 covers installing it.)

Reasonable starting points: set `requests` at roughly the p50–p75 of observed usage, and
`limits.memory` at the p99 plus headroom. Then look at whether you are throttling
(`container_cpu_cfs_throttled_periods_total`) and adjust.

The **Vertical Pod Autoscaler** in recommendation mode does this for you without changing
anything, and is worth running purely as an advisor (Chapter 25).

## Try it

Create one pod of each QoS class and see the classification:

```bash
kubectl apply -f examples/manifests/08-qos.yaml
```

```bash
kubectl get pods -l demo=qos -o custom-columns='NAME:.metadata.name,QOS:.status.qosClass'
```

Now reproduce the OOM finding. A container limited to 128Mi that allocates until killed:

```bash
kubectl apply -f examples/manifests/08-oom.yaml
```

```bash
sleep 40 && kubectl get pod oom3 -o jsonpath='{.status.containerStatuses[0].lastState}{"\n"}'
```

```
{"terminated":{"exitCode":137,"reason":"Error",...}}
```

Exit 137, reason `Error`. Now confirm from the node that it really was the OOM killer:

```bash
docker exec k8sbook-worker dmesg -T | grep -i 'memory cgroup out of memory' | tail -2
```

If your cluster reports `reason: OOMKilled` instead, good — but note that exit 137 was
correct either way.

See that scheduling uses requests, not usage:

```bash
kubectl describe node k8sbook-worker | sed -n '/Allocated resources/,/Events/p'
```

Compare with actual usage, which is a completely different number:

```bash
kubectl top node k8sbook-worker 2>/dev/null || echo "(needs metrics-server — Chapter 26)"
```

Clean up:

```bash
kubectl delete -f examples/manifests/08-qos.yaml -f examples/manifests/08-oom.yaml --force --grace-period=0
```

## Takeaways

- **Requests are for the scheduler; limits are for the kernel.** Scheduling never considers
  actual usage.
- CPU is compressible (throttled); memory is not (killed). Always set memory limits; set CPU
  limits deliberately, and avoid them for latency-sensitive work.
- CPU limits are a bandwidth quota — multi-threaded workloads freeze for the rest of each
  period, causing latency cliffs at low average utilisation.
- **Measured on v1.37/containerd 2.3.4/cgroup v2, an OOM kill reported `reason: Error`, not
  `OOMKilled`.** Exit code 137 plus the node's `Memory cgroup out of memory` log are the
  reliable signals. Do not rule out OOM because the reason field disagrees.
- QoS is derived: requests==limits → Guaranteed; some set → Burstable; **nothing set →
  BestEffort, evicted first**. Forgetting resources puts you in the worst class.
- `Guaranteed` unlocks exclusive CPU pinning for latency-sensitive workloads.
- Limit ephemeral storage, or one pod can evict everything on the node.
- LimitRange supplies defaults; ResourceQuota caps a namespace and *forces* requests to be
  specified once it exists.

---

Previous: [Chapter 7 — Configuration and secrets](07-config-and-secrets.md) ·
Next: [Chapter 9 — Health and lifecycle](09-health-and-lifecycle.md)

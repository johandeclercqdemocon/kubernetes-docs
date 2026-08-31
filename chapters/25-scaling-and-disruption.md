# Chapter 25 — Scaling and disruption

Two related problems: adding capacity when load rises, and not losing availability when the
cluster moves things around underneath you.

## Horizontal Pod Autoscaler

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: pingd
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: pingd
  minReplicas: 3
  maxReplicas: 20
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
```

The control loop: every 15 seconds, compute

```
desiredReplicas = ceil(currentReplicas × currentMetric / targetMetric)
```

**`averageUtilization: 70` means 70% of the pod's CPU *request*, not of a core and not of the
node.** A pod requesting `100m` and using `70m` is at 100% utilisation. This trips people up
constantly: an HPA appears to scale far too eagerly, and the cause is a request that is much
lower than actual usage.

So the HPA is only as good as your requests. Get Chapter 8 right first.

### Requirements and traps

**metrics-server must be installed.** Without it the HPA reports `<unknown>` and does nothing.

```bash
kubectl top pods
```

If that fails, the HPA cannot work.

**Remove `replicas` from your Deployment manifest.** This is the trap worth repeating from
Chapter 6: if `replicas: 3` is in the manifest and an HPA has scaled to 12, the next
`kubectl apply` or GitOps sync sets it back to 3, the HPA scales up again, and you have two
controllers fighting over one field. Under server-side apply you may instead get a conflict
error, which is better but still needs fixing. Delete the field.

**Scaling has asymmetric behaviour by design.** Scale-up is immediate; scale-down waits a
stabilisation window (300 s by default) to avoid flapping. Tune it explicitly:

```yaml
  behavior:
    scaleDown:
      stabilizationWindowSeconds: 300
      policies:
        - type: Percent
          value: 50
          periodSeconds: 60
    scaleUp:
      stabilizationWindowSeconds: 0
      policies:
        - type: Percent
          value: 100
          periodSeconds: 30
```

**CPU is a poor proxy for most services.** A latency-bound service waiting on a database uses
little CPU while being completely saturated. Scale on what actually indicates load — queue
depth, in-flight requests, or requests per second — via custom or external metrics:

```yaml
    - type: Pods
      pods:
        metric:
          name: http_inflight_requests
        target:
          type: AverageValue
          averageValue: "30"
```

This needs a metrics adapter (Prometheus Adapter, or KEDA). **KEDA** is worth knowing: it
scales on queue length, Kafka lag, cloud queue depth and dozens of other sources, and supports
scale-to-zero, which the HPA does not.

## Vertical Pod Autoscaler

Adjusts requests and limits rather than replica count.

```yaml
apiVersion: autoscaling.k8s.io/v1
kind: VerticalPodAutoscaler
metadata:
  name: pingd
spec:
  targetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: pingd
  updatePolicy:
    updateMode: "Off"        # recommend only
```

**`updateMode: "Off"` is the mode to use.** It computes recommendations and changes nothing,
which makes VPA an excellent free advisor for the sizing problem in Chapter 8:

```bash
kubectl describe vpa pingd | grep -A10 'Recommendation'
```

In `Auto` mode, VPA historically had to **evict pods** to change their resources, which is
disruptive and interacts badly with PDBs. In-place resizing is stabilising in recent versions
and changes this materially, but check what your cluster actually supports before enabling it.

**Do not run VPA in `Auto` mode and an HPA on CPU against the same workload.** They fight: VPA
raises the request, which lowers utilisation, which makes the HPA scale down, which raises
per-pod load, which makes VPA raise the request again. VPA on memory plus HPA on CPU is a
supported combination.

## Cluster Autoscaler and Karpenter

Pod autoscaling is useless if there is nowhere to put the pods.

**Cluster Autoscaler** watches for `Pending` pods that cannot schedule and adds nodes to a node
group; it removes nodes that have been underutilised for a period. It works within predefined
node groups, so instance types are fixed.

**Karpenter** provisions nodes directly from a set of constraints, choosing instance types to
fit the pending pods, and consolidates aggressively. It is generally faster and cheaper, and
it is much more willing to move your pods around — which makes PDBs and graceful shutdown
non-optional.

Things that block scale-down, and therefore cost money:

- Pods with no controller (bare pods) — nothing can recreate them.
- Pods with local storage (`emptyDir`) unless annotated as safe to evict.
- Pods a PDB will not permit to be evicted.
- `kube-system` pods without a PDB.

```yaml
metadata:
  annotations:
    cluster-autoscaler.kubernetes.io/safe-to-evict: "true"
```

A node stuck at low utilisation for weeks is usually one of those five, and the autoscaler's
logs say which.

## PodDisruptionBudgets

The mechanism that stops voluntary disruption from taking your service down.

```yaml
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: pingd
spec:
  maxUnavailable: 1
  selector:
    matchLabels:
      app: pingd
```

**Voluntary disruption** — node drains, autoscaler consolidation, `kubectl delete node` — goes
through the Eviction API and respects PDBs. **Involuntary disruption** — node failure, kernel
OOM, kubelet eviction under pressure — does **not**. A PDB is not a durability guarantee.

Chapter 21 measured a drain moving pods off a node. With a PDB, that drain blocks rather than
violating your minimum.

The rules that matter:

**Prefer `maxUnavailable: 1` over `minAvailable: N`.** A PDB with `minAvailable` equal to the
replica count can never be satisfied, so **drains block forever** and node upgrades become
incidents. `maxUnavailable` scales with the Deployment automatically.

**A single-replica Deployment with a PDB cannot be drained at all** without violating it. If
you have single-replica workloads, either accept the disruption or run two.

**Every workload that matters needs one**, including infrastructure. CoreDNS without a PDB can
lose all replicas to a drain (Chapter 12).

```bash
kubectl get pdb -A
```

```bash
kubectl get pdb pingd -o jsonpath='allowed={.status.disruptionsAllowed} current={.status.currentHealthy} desired={.status.desiredHealthy}{"\n"}'
```

`disruptionsAllowed: 0` means the next drain will block. That is worth alerting on — it is
usually the first sign that a workload is degraded.

## Topology spread

Replicas on one node means one node failure is an outage. Chapter 21's measurement showed all
three `pingd` pods landing on a single worker after a drain, and **staying there** because the
scheduler never rebalances.

```yaml
spec:
  topologySpreadConstraints:
    - maxSkew: 1
      topologyKey: kubernetes.io/hostname
      whenUnsatisfiable: ScheduleAnyway
      labelSelector:
        matchLabels:
          app: pingd
    - maxSkew: 1
      topologyKey: topology.kubernetes.io/zone
      whenUnsatisfiable: DoNotSchedule
      labelSelector:
        matchLabels:
          app: pingd
```

- **`maxSkew`** — the permitted difference in pod count between topology domains.
- **`whenUnsatisfiable: DoNotSchedule`** — hard requirement; the pod stays `Pending` if it
  cannot be satisfied.
- **`ScheduleAnyway`** — a preference.

Use `ScheduleAnyway` for hostname spreading (you would rather run unbalanced than not at all)
and `DoNotSchedule` for zone spreading if your availability model depends on it. This is the
modern replacement for pod anti-affinity, which is more expensive to evaluate and harder to
read.

Remember it applies **at scheduling time only**. After a drain, redistribution needs a
`kubectl rollout restart` — which is a good argument for doing one after any significant node
event.

## Try it

Check metrics-server, without which the HPA is inert:

```bash
kubectl top pods -l app=pingd 2>&1 | head -3
```

Create an HPA:

```bash
kubectl autoscale deployment pingd --min=3 --max=10 --cpu-percent=70
```

```bash
kubectl get hpa pingd
```

`<unknown>` in the TARGETS column means no metrics-server. Note what happens to the manifest
conflict — apply the original Deployment, which specifies `replicas: 3`:

```bash
kubectl apply -f examples/manifests/01-deployment.yaml && kubectl get hpa,deploy pingd
```

Now a PodDisruptionBudget, and see it block a drain:

```bash
kubectl apply -f examples/manifests/25-pdb.yaml && kubectl get pdb pingd
```

```bash
kubectl get pdb pingd -o jsonpath='allowed={.status.disruptionsAllowed} healthy={.status.currentHealthy}{"\n"}'
```

Try draining the node holding most replicas:

```bash
kubectl drain k8sbook-worker --ignore-daemonsets --delete-emptydir-data --timeout=60s
```

With `maxUnavailable: 1` it evicts one at a time and waits for replacements; with a stricter
budget it would block. Restore:

```bash
kubectl uncordon k8sbook-worker
```

Add topology spread and force redistribution:

```bash
kubectl patch deployment pingd --type=merge -p '{"spec":{"template":{"spec":{"topologySpreadConstraints":[{"maxSkew":1,"topologyKey":"kubernetes.io/hostname","whenUnsatisfiable":"ScheduleAnyway","labelSelector":{"matchLabels":{"app":"pingd"}}}]}}}}'
```

```bash
kubectl rollout status deployment/pingd && kubectl get pods -l app=pingd -o wide --no-headers | awk '{print $1, $7}'
```

Clean up:

```bash
kubectl delete hpa pingd; kubectl delete -f examples/manifests/25-pdb.yaml
```

## Takeaways

- HPA targets a percentage of the **CPU request**, not a core. Bad requests produce nonsense
  scaling.
- **Remove `replicas` from manifests managed by an HPA**, or the two fight.
- Scale-up is immediate; scale-down waits 300 s by default. Tune with `behavior`.
- CPU is a poor load signal for latency-bound services — scale on queue depth or in-flight
  requests, via Prometheus Adapter or KEDA (which also does scale-to-zero).
- **VPA in `updateMode: "Off"` is a free sizing advisor.** Do not combine `Auto` VPA with a
  CPU-based HPA.
- Cluster Autoscaler works within node groups; Karpenter provisions to fit and moves pods more
  aggressively — so PDBs and graceful shutdown matter more with it.
- **PDBs constrain voluntary disruption only.** Node failure ignores them.
- Prefer `maxUnavailable: 1`. `minAvailable` equal to replica count **blocks drains forever**.
- Alert on `disruptionsAllowed: 0`.
- Topology spread applies at scheduling time only; use `rollout restart` to rebalance after
  node events.

---

Previous: [Chapter 24 — Admission control and supply chain](24-admission-and-supply-chain.md) ·
Next: [Chapter 26 — Observability](26-observability.md)

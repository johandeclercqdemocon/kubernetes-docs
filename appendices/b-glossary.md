# Appendix B — Glossary

**Admission controller** — Code that inspects API requests after authorisation and before
persistence. Mutating runs first, then validating. *(Ch 24)*

**Allocatable** — A node's capacity minus reservations for the kubelet and OS. What the
scheduler actually uses. On kind it equals capacity; on production nodes it is 5–15% lower.
*(Ch 20)*

**Annotation** — Non-identifying metadata, not queryable. Contrast **label**. *(Ch 4)*

**BestEffort** — The QoS class for pods with no requests or limits. **Evicted first.** *(Ch 8)*

**Burstable** — QoS class where some requests are set but do not equal limits. *(Ch 8)*

**cgroup** — The kernel mechanism enforcing resource limits. Memory limits kill; CPU limits
throttle. *(Ch 8, 20)*

**CNI** — Container Network Interface. Gives pods IPs and routes pod-to-pod traffic without
NAT. **Determines whether NetworkPolicy is enforced at all.** *(Ch 3, 14, 31)*

**ConfigMap** — Non-secret configuration. Consumed as env vars (**frozen at container start**)
or files (**updated in ~1 min**). *(Ch 7)*

**Container Runtime Interface (CRI)** — How the kubelet talks to containerd or CRI-O. *(Ch 31)*

**Control loop** — Observe desired, observe actual, take one step to close the gap, repeat.
**Level-triggered**, so it survives missed events and restarts. The core idea. *(Ch 2)*

**CoreDNS** — Cluster DNS, running as an ordinary Deployment — and therefore a cluster-wide
failure domain. *(Ch 12)*

**CRD** — CustomResourceDefinition. Teaches the API server a new type. Cluster-scoped.
*(Ch 30)*

**CSI** — Container Storage Interface. Storage drivers. *(Ch 15)*

**DaemonSet** — One pod per node, automatically. Infrastructure DaemonSets need
`tolerations: [{operator: Exists}]` or they skip tainted nodes. *(Ch 10)*

**Deployment** — Manages ReplicaSets to run a stateless service with rollouts and rollbacks.
`spec.selector` is **immutable**. *(Ch 6)*

**Downward API** — Exposes pod metadata and **the container's own resource limits** to the
container. *(Ch 7)*

**EndpointSlice** — The actual list of a Service's backends. Not-ready pods stay listed with
`conditions.ready: false`. *(Ch 11)*

**etcd** — The only stateful component; every object is a key. Fails via slow disk
(cluster-wide latency), a 2 GiB quota (all writes rejected), or quorum loss. *(Ch 3, 21)*

**Eviction** — The kubelet deleting pods to recover node resources. Ranks by QoS, then usage
above requests, then priority. Distinct from **preemption**. *(Ch 20)*

**Ephemeral container** — A debugging container injected into a running pod. **Cannot be
removed.** *(Ch 18)*

**Finalizer** — A key blocking deletion until a controller does cleanup. A stale one blocks
deletion forever. *(Ch 21)*

**Gateway API** — The typed, role-separated successor to Ingress. Core is stable. *(Ch 13)*

**Guaranteed** — QoS class where requests equal limits for CPU and memory. Evicted last;
unlocks exclusive CPU pinning. *(Ch 8)*

**HPA** — HorizontalPodAutoscaler. Targets a percentage of the **CPU request**, not a core.
Needs metrics-server. Remove `replicas` from manifests it manages. *(Ch 25)*

**Ingress** — HTTP routing to Services. Does nothing without a controller. Everything beyond
host/path/TLS is **controller-specific annotations, silently ignored when misspelled**.
*(Ch 13)*

**Init container** — Runs to completion before app containers, in order. May use a different
image. *(Ch 5)*

**Job** — Runs pods until N complete. Always set `ttlSecondsAfterFinished`. *(Ch 10)*

**kube-proxy** — Implements Service ClusterIPs as node-level rules. Broken on one node: that
node cannot reach any ClusterIP but *can* reach pod IPs. *(Ch 3, 19)*

**kubelet** — The node agent. Creates containers, **runs probes locally**, reports status,
evicts under pressure, runs static pods. *(Ch 3)*

**Label / selector** — Key-value identification, and the queries over it. **The coupling
between nearly every Kubernetes object.** A selector matching nothing is never an error.
*(Ch 4)*

**LimitRange** — Namespace defaults, minimums and maximums for container resources. Pair with
ResourceQuota so existing manifests keep working. *(Ch 8, 28)*

**Liveness probe** — "Is it wedged?" Failing **restarts** the container. **Must not check
dependencies.** *(Ch 9)*

**Namespace** — Scopes names, RBAC, quotas and policy. **Not a security, network or kernel
boundary.** *(Ch 4, 28)*

**NetworkPolicy** — Additive allow-lists. Selecting a pod with no rules denies that direction.
Uses the **pod** port, not the Service port. Requires DNS to be allowed in egress. *(Ch 14)*

**Operator** — A controller reconciling a custom resource. Value is **encoded operational
knowledge**. *(Ch 30)*

**ownerReferences** — What drives cascading deletion. Deleting the middle of a chain does not
stick. *(Ch 2)*

**PDB** — PodDisruptionBudget. Constrains **voluntary** disruption only (drains, autoscaler
consolidation) — never node failure. `minAvailable` == replicas blocks drains forever.
*(Ch 25)*

**Pod** — Containers sharing a network namespace, IPC and volumes, co-scheduled and
co-terminated. **Not** a shared PID namespace by default. The unit of scheduling. *(Ch 5)*

**Pod Security Standards** — `privileged`/`baseline`/`restricted`, applied by namespace label
with `enforce`/`warn`/`audit` modes. *(Ch 23)*

**Preemption** — The scheduler evicting lower-priority pods to make room. Distinct from
**eviction**. *(Ch 20)*

**PV / PVC** — PersistentVolume (actual storage) and PersistentVolumeClaim (the request). Pods
reference PVCs. *(Ch 15)*

**QoS class** — Derived, not declared: Guaranteed, Burstable, BestEffort. Determines eviction
order. *(Ch 8)*

**Readiness probe** — "Can it serve now?" Failing marks it **not-ready in EndpointSlices**.
**Gates rollouts** — without one, a broken version rolls out completely. *(Ch 9)*

**ReplicaSet** — Keeps N pods alive. Managed by a Deployment; old ones are retained at zero
replicas so rollback is instant. *(Ch 6)*

**Requests vs limits** — **Requests are for the scheduler; limits are for the kernel.**
Scheduling never considers actual usage. *(Ch 8)*

**ResourceQuota** — Caps a namespace's total consumption. **Once it exists, every pod must
specify the quotaed resource.** *(Ch 28)*

**Secret** — Base64-**encoded**, not encrypted; unencrypted in etcd by default. `list` on
Secrets reads them all. *(Ch 7, 23)*

**Service** — Stable name and virtual IP over a changing set of pods, matched **by label
selector**. Balances **per connection**, which is why gRPC pins to one pod. *(Ch 11)*

**ServiceAccount** — A pod's identity. Its token is **mounted into every pod by default** even
when unused. *(Ch 23)*

**StatefulSet** — Stable ordinal names, stable per-pod DNS and PVCs, ordered operations. PVCs
survive deletion deliberately. *(Ch 10)*

**Static pod** — A pod started by the kubelet from a manifest on disk, owned by the **Node**.
How the control plane bootstraps, and the escape hatch when the API server is down. *(Ch 3)*

**Taint / toleration** — Taints repel pods from a node; tolerations permit them. `NoExecute`
taints applied by the node controller drive the ~5-minute failover. *(Ch 5, 21)*

**Topology spread constraints** — Distribute replicas across nodes or zones. Applies **at
scheduling time only**. *(Ch 25)*

**Validating Admission Policy** — CEL-based admission built into Kubernetes, **no webhook
required**. *(Ch 24)*

**VPA** — VerticalPodAutoscaler. In `updateMode: "Off"` it is a free sizing advisor. Do not
combine `Auto` mode with a CPU-based HPA. *(Ch 25)*

---

[Back to contents](../README.md) · Previous: [Appendix A](a-cheatsheet.md) ·
Next: [Appendix C — Further reading](c-further-reading.md)

# Chapter 26 — Observability

A cluster produces four kinds of signal: metrics, logs, events and traces. Kubernetes ships
almost none of the infrastructure to collect them — it produces the data and expects you to
bring the stack.

## The gap you notice first

```bash
kubectl top pods
```

```
error: Metrics API not available
```

That is a default cluster. `kubectl top` needs **metrics-server**, which is not installed by
default anywhere except some managed offerings. Neither is anything that stores logs, keeps
events beyond an hour, or traces requests.

```bash
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
```

metrics-server provides *only* current CPU and memory for pods and nodes — enough for
`kubectl top` and the HPA (Chapter 25), and nothing else. It stores no history. It is a
prerequisite, not a monitoring system.

## Metrics

**Prometheus** is the de facto standard, usually via kube-prometheus-stack, which bundles
Prometheus, Alertmanager, Grafana, node-exporter and kube-state-metrics with dashboards and
alert rules already written.

Three distinct sources, and knowing which is which saves confusion:

- **kubelet / cAdvisor** — actual container resource usage:
  `container_cpu_usage_seconds_total`, `container_memory_working_set_bytes`,
  **`container_cpu_cfs_throttled_periods_total`**.
- **kube-state-metrics** — the *state of API objects*: `kube_pod_status_phase`,
  `kube_deployment_status_replicas_available`, `kube_pod_container_status_restarts_total`.
  This is object state, not resource usage, and it is what most alerting is built on.
- **Your application** — request rate, error rate, latency. Nothing else can tell you these.

Scraping is declarative with the Prometheus Operator:

```yaml
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: pingd
spec:
  selector:
    matchLabels:
      app: pingd
  endpoints:
    - port: metrics
      interval: 30s
```

The trap: **`ServiceMonitor` selects a Service, and that Service must expose the metrics
port**. A ServiceMonitor whose selector matches nothing produces no error — exactly the silent
failure of Chapter 19, in a different guise. Check `Status → Targets` in Prometheus.

## Logs

```bash
kubectl logs -f --tail=100 deploy/pingd
kubectl logs POD -c CONTAINER --previous
kubectl logs -l app=pingd --prefix --tail=20
```

`kubectl logs` reads files on the node written by the container runtime. Those files are
rotated by the kubelet (10 MB per file, 5 files by default) and **deleted when the pod is
deleted**. So logs from a pod that no longer exists are gone, which is precisely when you want
them — after a crash, a rollout, or an eviction.

Shipping them elsewhere is not optional in production. The standard pattern is a **DaemonSet
collector** — Fluent Bit, Vector, Promtail, Filebeat — reading `/var/log/containers/` on each
node and forwarding to Loki, Elasticsearch/OpenSearch, or a managed service.

Everything the Docker book said applies unchanged: **log to stdout**, use **structured JSON**,
set `PYTHONUNBUFFERED=1` or the equivalent, and never log secrets. Kubernetes adds one thing
worth doing — enrich each line with pod, namespace, node and container, which collectors do
automatically from the file path and the API.

## Events

Events are the cluster's own narration (Chapter 16), and they are **deleted after about an
hour**:

```bash
kubectl get events --sort-by=.lastTimestamp -A | tail -20
```

That retention makes them useless for post-incident analysis unless you export them. Ship them
like logs — most collectors have a Kubernetes events input, and `kubernetes-event-exporter` is
purpose-built.

Events worth alerting on directly: `Failed`, `FailedScheduling`, `Unhealthy`, `OOMKilling`,
`Evicted`, `BackOff`, `FailedMount`.

## Traces

OpenTelemetry, as in the Docker book. What Kubernetes adds is the ability to correlate a span
with the pod that produced it — inject identity via the downward API (Chapter 7):

```yaml
env:
  - name: OTEL_RESOURCE_ATTRIBUTES
    value: "k8s.pod.name=$(POD_NAME),k8s.namespace.name=$(POD_NAMESPACE),k8s.node.name=$(NODE_NAME)"
```

The OpenTelemetry Operator can auto-instrument workloads by annotation, without changing
images — worth knowing before you plan an instrumentation project.

## What to alert on

Symptoms, not causes. "CPU is high" is not an incident; "the error rate is 5%" is.

**Workload health**

```promql
# Pods restarting repeatedly
increase(kube_pod_container_status_restarts_total[15m]) > 3
```

```promql
# Deployment below desired replicas for 15 minutes
kube_deployment_status_replicas_available < kube_deployment_spec_replicas
```

```promql
# Pods stuck Pending
kube_pod_status_phase{phase="Pending"} > 0
```

**Resource pressure — the two that hide**

```promql
# CPU throttling: latency with low utilisation (Chapter 20)
rate(container_cpu_cfs_throttled_periods_total[5m])
  / rate(container_cpu_cfs_periods_total[5m]) > 0.25
```

```promql
# Approaching the memory limit before it OOMs
container_memory_working_set_bytes
  / container_spec_memory_limit_bytes > 0.9
```

Both matter because `kubectl top` cannot show throttling at all, and an OOM kill may report
`reason: Error` rather than `OOMKilled` (Chapter 20).

**Cluster health**

```promql
kube_node_status_condition{condition="Ready",status="true"} == 0
```

```promql
# A PDB that will block the next drain (Chapter 25)
kube_poddisruptionbudget_status_pod_disruptions_allowed == 0
```

```promql
# Certificate expiry — the one-year cliff from Chapter 21
apiserver_client_certificate_expiration_seconds_bucket
```

```promql
# etcd disk latency — presents as whole-cluster slowness
histogram_quantile(0.99, rate(etcd_disk_wal_fsync_duration_seconds_bucket[5m])) > 0.5
```

**Do not alert on** node CPU utilisation, pod count, or memory usage without reference to
limits. They generate noise and train people to ignore alerts.

## Dashboards worth having

- **Cluster capacity** — allocatable vs requests vs actual usage, per node. This single view
  answers most "why won't it schedule" questions.
- **Per-workload** — replicas desired/available, restarts, CPU and memory against requests and
  limits, **throttling percentage**.
- **Golden signals per service** — rate, errors, duration, saturation.
- **Control plane** — API latency, etcd fsync, scheduler queue depth.

## A minimal stack

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm install monitoring prometheus-community/kube-prometheus-stack -n monitoring --create-namespace
```

```bash
helm repo add grafana https://grafana.github.io/helm-charts
helm install loki grafana/loki-stack -n monitoring
```

That is metrics, alerting, dashboards and logs in two commands. Add metrics-server separately —
kube-prometheus-stack does not include it, and the HPA needs it.

Give the monitoring stack **resource requests and a PDB**. Monitoring that is BestEffort gets
evicted exactly when the cluster is under pressure, which is when you need it (Chapter 20).

## Cost visibility

Worth a mention because it is invisible by default and material in practice. OpenCost and
Kubecost attribute node cost to namespaces, workloads and labels using requests and usage. The
usual finding is that requests are set far above real usage, and that the gap is most of the
bill. Chapter 25's VPA in recommendation mode is the cheap way to act on it.

## Try it

Confirm the gap:

```bash
kubectl top pods
```

```
error: Metrics API not available
```

Install metrics-server. On kind, the kubelet's serving certificate is self-signed, so it needs
`--kubelet-insecure-tls` — a local-cluster concession, not a production setting:

```bash
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
```

```bash
kubectl patch deployment metrics-server -n kube-system --type=json -p='[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]'
```

```bash
kubectl rollout status deployment/metrics-server -n kube-system --timeout=180s
```

```bash
kubectl top nodes; kubectl top pods -l app=pingd --containers
```

Now compare the two numbers Chapter 8 insisted were different — requests (what the scheduler
sees) versus usage (what is happening):

```bash
kubectl describe node k8sbook-worker | sed -n '/Allocated resources/,/Events/p' | head -8
```

```bash
kubectl top node k8sbook-worker
```

Read the raw per-container metrics the kubelet exposes — this is the endpoint metrics-server
scrapes:

```bash
kubectl get --raw "/api/v1/nodes/k8sbook-worker/proxy/metrics/resource" | grep -m3 '^container'
```

```
container_cpu_usage_seconds_total{container="kube-proxy",namespace="kube-system",...}
container_memory_working_set_bytes{container="kindnet-cni",namespace="kube-system",...}
```

Note what is **not** there: the `/metrics/resource` endpoint carries CPU and memory only. The
throttling counters (`container_cpu_cfs_throttled_periods_total`) come from the kubelet's
cAdvisor endpoint (`/metrics/cadvisor`) and are only emitted for containers with CPU limits
that have actually been throttled — which is why you need Prometheus scraping cAdvisor to
alert on throttling, and why it stays invisible until you do.

And see how short event retention is:

```bash
kubectl get events -A --sort-by=.lastTimestamp -o custom-columns='AGE:.lastTimestamp,REASON:.reason,OBJECT:.involvedObject.name' | head -5
```

Nothing older than about an hour.

## Takeaways

- A default cluster has **no metrics, no log storage, no event retention, no tracing**.
  `kubectl top` fails until you install metrics-server.
- metrics-server serves current CPU/memory only — a prerequisite for `top` and the HPA, not a
  monitoring system.
- Three metric sources: kubelet/cAdvisor (usage), kube-state-metrics (object state), your app
  (golden signals). You need all three.
- A `ServiceMonitor` whose selector matches nothing fails **silently**. Check Prometheus
  targets.
- **Pod logs are deleted with the pod.** Ship them off-node or lose them exactly when it
  matters.
- **Events expire after ~1 hour.** Export them or you cannot do post-incident analysis.
- Alert on symptoms. The two hidden ones worth explicit rules: **CPU throttling ratio** and
  **memory approaching the limit**.
- Give your monitoring stack requests and a PDB, or it is evicted when you need it most.

---

Previous: [Chapter 25 — Scaling and disruption](25-scaling-and-disruption.md) ·
Next: [Chapter 27 — Deployment strategies and GitOps](27-deployment-and-gitops.md)

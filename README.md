# Kubernetes: From First Pod to Production

A working book on Kubernetes — what the control loop actually does, how to run workloads
on it, how to debug them when they misbehave, and what production requires that tutorials
leave out.

Written against **Kubernetes v1.37.0**, measured on a three-node `kind` cluster
(containerd 2.3.4, Debian trixie nodes). Where managed clusters differ from a local one,
chapters say so.

This is the companion to [docker-docs](https://github.com/johandeclercqdemocon/docker-docs),
which covers images, containers and the single-host story. That book's Chapter 30 argued
you should not adopt Kubernetes without a present constraint. This one assumes you have
one.

## Who this is for

Someone comfortable with containers who now has to run them on a cluster. You do not need
to have read the Docker book, but this one assumes you know what an image, a layer and a
container are, and that a container is a process with namespaces and cgroups.

If you already run Kubernetes daily, Parts IV, V and VI are the ones worth your time.

## The approach

**Every measurement in this book was produced by running the command.** Where a result
contradicted what I expected, or contradicted common advice, the chapter says so and shows
the output. Kubernetes has a large body of received wisdom, some of it from versions that
are years out of date; the only defence is to check.

Chapters end with **Try it** (runnable against your own cluster) and **Takeaways**.

Prose rots. [`scripts/check_snippets.py`](scripts/check_snippets.py) checks what can be
checked mechanically — that every internal link resolves, every `bash` block parses, and
every `-f examples/...` file referenced by a command exists:

```bash
python3 scripts/check_snippets.py
```

`--run` additionally executes the commands against a live cluster, skipping those marked
destructive and those containing placeholders.

## The cluster

Everything here runs on a local `kind` cluster you can recreate in about a minute:

```bash
kind create cluster --config examples/cluster/kind-cluster.yaml
```

Three nodes, so scheduling, node affinity, disruption and failure are real rather than
hypothetical. See [`examples/cluster/`](examples/cluster/).

## The running example

`pingd` — the same small HTTP service used in the Docker book, now deployed to a cluster.
It grows across the chapters: a bare Pod, then a Deployment, a Service, configuration,
probes, an Ingress, autoscaling, RBAC and a PodDisruptionBudget. Manifests live in
[`examples/manifests/`](examples/manifests/), numbered in the order they appear.

Part IV and V also draw on two heavier cases where a toy example hides the problem: a
**SIP load-test workload** (host networking, UDP, latency sensitive to CPU throttling and
scheduling jitter) and an **LLM evaluation runner** (long jobs, expensive caches, Jobs that
must not be interrupted mid-flight).

---

## Contents

### Part I — Foundations

| # | Chapter | What it covers |
|---|---------|----------------|
| 1 | [Why Kubernetes](chapters/01-why-kubernetes.md) | The actual problem, the actual cost, and when not to |
| 2 | [Declarative reconciliation](chapters/02-reconciliation.md) | Control loops, desired vs actual, why `kubectl apply` is not a command |
| 3 | [Cluster anatomy](chapters/03-cluster-anatomy.md) | API server, etcd, scheduler, controller manager, kubelet, kube-proxy |
| 4 | [kubectl and the resource model](chapters/04-kubectl-and-resources.md) | Objects, namespaces, labels, selectors, the API |

### Part II — Running workloads

| # | Chapter | What it covers |
|---|---------|----------------|
| 5 | [Pods](chapters/05-pods.md) | The atom: shared namespaces, multi-container patterns, init containers |
| 6 | [Deployments and ReplicaSets](chapters/06-deployments.md) | Rollouts, rollbacks, the selector trap |
| 7 | [Configuration and secrets](chapters/07-config-and-secrets.md) | ConfigMaps, Secrets, downward API, and what Secrets are not |
| 8 | [Resources, requests and QoS](chapters/08-resources-and-qos.md) | Scheduling, throttling, OOM, and the three QoS classes |
| 9 | [Health and lifecycle](chapters/09-health-and-lifecycle.md) | Three probes, graceful shutdown, the endpoint-removal race |
| 10 | [The other workload kinds](chapters/10-other-workloads.md) | Jobs, CronJobs, DaemonSets, StatefulSets |

### Part III — Networking and storage

| # | Chapter | What it covers |
|---|---------|----------------|
| 11 | [Services](chapters/11-services.md) | ClusterIP, NodePort, LoadBalancer, headless, EndpointSlices |
| 12 | [DNS and service discovery](chapters/12-dns.md) | CoreDNS, search domains, `ndots:5`, and the latency it causes |
| 13 | [Ingress and Gateway API](chapters/13-ingress.md) | HTTP routing, TLS, and the successor to Ingress |
| 14 | [NetworkPolicy](chapters/14-networkpolicy.md) | Default-allow, and how to change it |
| 15 | [Storage](chapters/15-storage.md) | Volumes, PV/PVC, StorageClasses, and stateful reality |

### Part IV — Debugging

| # | Chapter | What it covers |
|---|---------|----------------|
| 16 | [The debugging mindset](chapters/16-debugging-mindset.md) | Five layers, and the triage sequence |
| 17 | [Pods that won't run](chapters/17-pods-wont-run.md) | Pending, ImagePullBackOff, CrashLoopBackOff, CreateContainerConfigError |
| 18 | [Getting inside](chapters/18-getting-inside.md) | exec, ephemeral containers, `kubectl debug`, distroless |
| 19 | [Network diagnosis](chapters/19-debugging-networks.md) | Service has no endpoints, DNS, policy, and the selector mismatch |
| 20 | [Resources and eviction](chapters/20-resources-and-eviction.md) | OOMKilled, throttling, node pressure, preemption |
| 21 | [Nodes and the control plane](chapters/21-nodes-and-control-plane.md) | NotReady, taints, etcd, API server, certificates |
| 22 | [Cookbook: symptom → cause → fix](chapters/22-cookbook.md) | Indexed by what you actually see |

### Part V — Production

| # | Chapter | What it covers |
|---|---------|----------------|
| 23 | [Security](chapters/23-security.md) | RBAC, ServiceAccounts, Pod Security Standards, securityContext |
| 24 | [Admission control and supply chain](chapters/24-admission-and-supply-chain.md) | Webhooks, policy engines, image verification |
| 25 | [Scaling and disruption](chapters/25-scaling-and-disruption.md) | HPA, VPA, Cluster Autoscaler, PDBs, topology spread |
| 26 | [Observability](chapters/26-observability.md) | Metrics, logs, events, traces, and what to alert on |
| 27 | [Deployment strategies and GitOps](chapters/27-deployment-and-gitops.md) | Rolling, blue/green, canary, Argo CD and Flux |
| 28 | [Multi-tenancy](chapters/28-multi-tenancy.md) | Namespaces, quotas, LimitRanges, and their limits |

### Part VI — Beyond the basics

| # | Chapter | What it covers |
|---|---------|----------------|
| 29 | [Packaging: Helm and Kustomize](chapters/29-helm-and-kustomize.md) | Templating vs overlays, and when each hurts |
| 30 | [Operators and CRDs](chapters/30-operators-and-crds.md) | Extending the API, and whether you should |
| 31 | [The ecosystem](chapters/31-ecosystem.md) | Distributions, service mesh, managed offerings, CNCF |
| 32 | [Anti-patterns](chapters/32-anti-patterns.md) | The catalogue, with reasons and a review checklist |

### Appendices

- [A — kubectl cheatsheet](appendices/a-cheatsheet.md)
- [B — Glossary](appendices/b-glossary.md)
- [C — Further reading](appendices/c-further-reading.md)

---

## Conventions

Commands run against the cluster:

```bash
kubectl get pods
```

Output is shown when it is the point:

```
NAME                     READY   STATUS    RESTARTS   AGE
pingd-5975cc6496-fmfvv   1/1     Running   0          2m
```

**⚠️ destructive** marks anything that deletes data or is hard to undo, with the blast
radius stated before the command.

Placeholders are `UPPERCASE`; real runnable values are lowercase.

## Licence

MIT. See [LICENSE](LICENSE).

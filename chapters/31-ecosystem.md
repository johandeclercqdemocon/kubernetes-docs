# Chapter 31 — The ecosystem

Kubernetes is deliberately incomplete. It defines interfaces — for containers, networking,
storage, and increasingly for gateways and policy — and expects implementations to be plugged
in. That is why "install Kubernetes" is never the end of the work, and why the landscape has
several hundred projects in it.

This chapter is a map, with opinions.

## The interfaces

**CRI (Container Runtime Interface)** — how the kubelet talks to a runtime.
**containerd** is the default nearly everywhere; **CRI-O** is Kubernetes-only and the default
on OpenShift. Dockershim was removed in v1.24; images are unaffected because they are OCI
images either way (Docker book, Chapter 31).

**CNI (Container Network Interface)** — pod networking. Your choice determines whether
NetworkPolicy works at all (Chapter 14):

| CNI | Notes |
|---|---|
| **Cilium** | eBPF-based; can replace kube-proxy; L7 policy, DNS-based egress, Hubble observability. The current default recommendation. |
| **Calico** | Mature, widely deployed, good policy support, optional eBPF mode. |
| **Flannel** | Simple, and **does not enforce NetworkPolicy**. |
| **AWS VPC CNI / Azure CNI** | Pods get real VPC addresses. Native integration; watch IP exhaustion. |

**CSI (Container Storage Interface)** — storage drivers, per Chapter 15.

**Gateway API** — the typed replacement for Ingress (Chapter 13).

## Distributions

**Managed control planes** — EKS, GKE, AKS, DigitalOcean, Linode. The control plane, etcd
backups, and control-plane upgrades are someone else's problem. **This is the right default
for most organisations**; the differentiating value of running your own control plane is
almost always negative.

GKE Autopilot and EKS Auto Mode go further and manage nodes too, charging per pod. For teams
without platform engineers this is often the correct answer.

**Lightweight** — k3s (a single binary, production-capable, excellent for edge and small
clusters), k0s, MicroK8s. k3s in particular deserves consideration where a full cluster is
overkill but you want the real API.

**Local** — kind (used throughout this book), minikube, k3d, Docker Desktop's built-in
cluster. kind is the best fit for CI because it is fast and disposable.

**Enterprise** — OpenShift (an opinionated platform with its own build and routing systems),
Rancher, Tanzu. You are buying support, integration and a security posture.

## Service mesh

Sidecar or per-node proxies giving you mTLS, retries, timeouts, circuit breaking, traffic
splitting and detailed L7 telemetry.

**Istio** — the most capable and the most complex. Ambient mode removes per-pod sidecars,
which materially reduces the resource overhead and operational weight that made classic Istio
hard to justify.

**Linkerd** — deliberately simpler, Rust micro-proxy, very low overhead. If you want mTLS and
golden-signal metrics without a project, this is the one.

**Cilium Service Mesh** — mesh features in the CNI, no sidecars.

**Should you?** A mesh solves real problems: mTLS between services, retries and timeouts
without touching application code, and L7 metrics for everything. It also adds a proxy to every
request path, meaningful resource overhead, and a substantial new failure domain — mesh
misconfiguration causes outages that are genuinely hard to diagnose.

Adopt one when you have a **specific** need: a compliance requirement for mTLS, or enough
services that per-application retry logic is unmanageable. "Microservices best practice" is
not a reason. Note also that the Gateway API (Chapter 13) now covers traffic splitting without
a mesh, which removes one of the historically common justifications.

## What you will actually install

A realistic list for a production cluster, roughly in order of adoption:

| Need | Common choice |
|---|---|
| Metrics, alerting, dashboards | kube-prometheus-stack |
| Basic resource metrics / HPA | metrics-server (separate!) |
| Log aggregation | Loki, or a managed service |
| Ingress | ingress-nginx, or a Gateway API implementation |
| Certificates | **cert-manager** — install on day one |
| Secrets | External Secrets Operator, or Sealed Secrets |
| Deployment | Argo CD or Flux |
| Policy | Kyverno, or built-in ValidatingAdmissionPolicy |
| Node autoscaling | Karpenter (AWS) or Cluster Autoscaler |
| Backup | Velero |
| Cost | OpenCost / Kubecost |
| Image scanning | Trivy Operator |

Two notes. **cert-manager** is the least controversial thing on that list — automatic
certificate issuance and renewal, and everything else assumes it. And **Velero** is the one
people install after they needed it: cluster-level backup and restore of resources plus
volume snapshots.

## Choosing from the landscape

The CNCF landscape has hundreds of entries and is not a shortlist. A practical filter:

**Prefer graduated or incubating CNCF projects.** Sandbox projects may be excellent and may be
abandoned in a year.

**Check the maintenance signal**, not the star count: recent releases, issue response, more
than one active maintainer, and a security policy.

**Understand the business model.** Open-core projects have features you will eventually want
behind a paid tier. That is legitimate; know it before standardising.

**Prefer fewer components.** Every addition is something to upgrade, monitor, secure and debug
at 3am. The built-in ValidatingAdmissionPolicy (Chapter 24) instead of a policy engine, or
Gateway API instead of a mesh, are real wins when they suffice.

**Ask what happens when it stops.** As Chapter 30 put it: a good component's absence should be
inert.

## Where things are heading

Stated with appropriate uncertainty:

**Gateway API replacing Ingress** for new deployments. Core is stable; adoption is broad.

**eBPF everywhere.** Cilium's approach — networking, policy, observability and mesh in the
kernel without sidecars — is clearly the direction.

**Sidecar-less meshes.** Istio ambient and Cilium remove the per-pod proxy that made meshes
expensive.

**In-place pod resizing**, which makes VPA usable without evictions (Chapter 25).

**Supply chain enforcement becoming mandatory** rather than good practice (Chapter 24).

**WebAssembly** as an additional workload type — smaller, faster to start, sandboxed by
design. Real, interesting, still maturing, and a complement rather than a replacement.

## The honest summary

Kubernetes is a platform for building platforms. The API and the control loop are excellent
and stable. Everything above them is an ecosystem of varying quality that you must assemble,
operate and keep current.

That assembly work is why "platform engineering" exists as a job, and why Chapter 1 argued
against adopting Kubernetes without a present constraint. If you do have one, the ecosystem is
a genuine asset — nearly every problem has a mature, well-understood solution. The skill is
choosing few of them.

## Try it

See the interfaces in use on your own cluster:

```bash
kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name}{" runtime="}{.status.nodeInfo.containerRuntimeVersion}{"\n"}{end}'
```

```
k8sbook-control-plane runtime=containerd://2.3.4
```

Which CNI is running:

```bash
kubectl get pods -n kube-system -o custom-columns='NAME:.metadata.name,IMAGE:.spec.containers[0].image' | grep -Ei 'cilium|calico|flannel|kindnet'
```

What API groups your cluster serves — this is the extension surface:

```bash
kubectl api-versions | sort | head -25
```

And which of those came from CRDs rather than core Kubernetes:

```bash
kubectl get crd -o custom-columns='NAME:.metadata.name,GROUP:.spec.group' --no-headers | head
```

On this book's cluster that shows the ingress controller's CRDs — everything else is built in.

Check what a managed cluster would hide from you:

```bash
kubectl get pods -n kube-system -l tier=control-plane
```

On EKS/GKE/AKS this returns nothing, because the control plane is not yours to see.

## Takeaways

- Kubernetes defines interfaces (CRI, CNI, CSI, Gateway API) and expects implementations.
  "Install Kubernetes" is never the whole job.
- **Your CNI choice determines whether NetworkPolicy works.** Flannel does not enforce it.
- **Managed control planes are the right default.** Running your own rarely differentiates
  anything.
- k3s is a strong middle path; kind is the best local and CI cluster.
- A service mesh solves real problems and adds a proxy to every request and a large failure
  domain. Adopt for a specific need — Gateway API now covers traffic splitting without one.
- cert-manager on day one. metrics-server is **separate** from kube-prometheus-stack.
- Filter the landscape by maintenance signal, business model, and **what happens when it
  stops**. Prefer fewer components.
- Direction of travel: Gateway API, eBPF, sidecar-less meshes, in-place resizing, mandatory
  supply chain.

---

Previous: [Chapter 30 — Operators and CRDs](30-operators-and-crds.md) ·
Next: [Chapter 32 — Anti-patterns](32-anti-patterns.md)

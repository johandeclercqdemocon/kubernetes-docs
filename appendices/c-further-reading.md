# Appendix C — Further reading

## Primary documentation

- **Kubernetes docs** — <https://kubernetes.io/docs/>. The Concepts section rewards reading in
  order rather than dipping into. The API reference is generated and authoritative.
- **`kubectl explain`** — the documentation for *your* cluster version, always correct, always
  available offline. Use it before searching.
- **Kubernetes Enhancement Proposals** — <https://github.com/kubernetes/enhancements>. Where
  features are designed. The KEP for a feature explains *why* it works the way it does, which
  the user docs rarely do.
- **API deprecation guide** — <https://kubernetes.io/docs/reference/using-api/deprecation-guide/>.
  Read before every upgrade (Ch 21).
- **Gateway API** — <https://gateway-api.sigs.k8s.io/>.

## Understanding the internals

- **`kubernetes/community` design proposals** — <https://github.com/kubernetes/community>.
  The original architecture documents.
- **Kelsey Hightower, *Kubernetes The Hard Way*** —
  <https://github.com/kelseyhightower/kubernetes-the-hard-way>. Build a cluster component by
  component with no tooling. The single best way to make Chapter 3 concrete.
- **Brendan Burns et al., *Kubernetes: Up and Running*** (O'Reilly) — solid general
  introduction.
- **Marko Lukša, *Kubernetes in Action*** (Manning) — still the best explanatory book,
  particularly on the object model.
- **Jeff Geerling / Learnk8s resources** — good practical material on sizing and capacity.

## Operations and production

- **Google SRE Book** — <https://sre.google/books/>. Not Kubernetes-specific, and the right
  frame for what to alert on (Ch 26).
- **Learnk8s Production Best Practices checklist** —
  <https://learnk8s.io/production-best-practices>. Overlaps heavily with Chapter 32's
  checklist and is worth cross-referencing.
- **Kubernetes Failure Stories** — <https://k8s.af/>. Real post-mortems. More instructive than
  any best-practice list, and a good antidote to overconfidence.
- **CIS Kubernetes Benchmark** — the hardening checklist your auditor has read.
- **NSA/CISA Kubernetes Hardening Guidance** — free, thorough, and reasonable.

## Security

- **Pod Security Standards** —
  <https://kubernetes.io/docs/concepts/security/pod-security-standards/>.
- **RBAC docs** — <https://kubernetes.io/docs/reference/access-authn-authz/rbac/>.
- **`kubectl-who-can`** (Aqua) — reverse RBAC lookup: who can perform this action?
- **`rbac-lookup`**, **`rakkess`** — audit bindings and access matrices.
- **kube-bench** — automates the CIS benchmark.
- **kube-hunter**, **Trivy Operator** — cluster vulnerability scanning.
- **Sigstore / cosign** — <https://docs.sigstore.dev/>, for Chapter 24's verification.

## Networking

- **Cilium docs** — <https://docs.cilium.io/>. Among the best technical writing in the
  ecosystem, and useful even if you run a different CNI.
- **Calico's networking guides** — good on BGP, IPAM and policy.
- **"A Guide to the Kubernetes Networking Model"** (Julia Evans' and others' write-ups) — for
  the mental model.
- **NetworkPolicy editor** — <https://editor.networkpolicy.io/>. Visualises what a policy
  actually does, which catches the AND/OR list-item trap from Chapter 14.

## Tools referenced in this book

| Tool | For | Where |
|---|---|---|
| `kind` | Local/CI clusters | kind.sigs.k8s.io |
| `k9s` | Terminal UI | k9scli.io |
| `kubectx` / `kubens` | Context and namespace switching | github.com/ahmetb/kubectx |
| `kube-ps1` / starship | Current context in your prompt | — |
| `stern` | Multi-pod log tailing | github.com/stern/stern |
| `netshoot` | Debugging toolbox image | github.com/nicolaka/netshoot |
| `kustomize` | Overlays (built into kubectl) | kustomize.io |
| `helm` | Packaging | helm.sh |
| `helm diff` | Preview upgrades — install this | github.com/databus23/helm-diff |
| `Argo CD` / `Flux` | GitOps | argo-cd.readthedocs.io / fluxcd.io |
| `Argo Rollouts` / `Flagger` | Progressive delivery with rollback | — |
| `Kyverno` | Policy as YAML | kyverno.io |
| `cert-manager` | Certificates — install on day one | cert-manager.io |
| `External Secrets Operator` | Secrets from Vault/cloud | external-secrets.io |
| `Velero` | Backup and restore | velero.io |
| `metrics-server` | `kubectl top` and HPA | github.com/kubernetes-sigs/metrics-server |
| `kube-prometheus-stack` | Metrics, alerts, dashboards | github.com/prometheus-operator |
| `OpenCost` / `Kubecost` | Cost attribution | opencost.io |
| `pluto` / `kubent` | Deprecated API detection | — |
| `vCluster` | Virtual clusters for tenancy | vcluster.com |
| `polaris` / `kubescape` | Manifest linting against best practice | — |

`polaris` deserves a specific mention: it checks manifests against a large fraction of
Chapter 32's checklist automatically, and belongs in CI.

## Certification, if useful

- **CKA** — cluster operations. Practical, hands-on, and genuinely a good forcing function for
  Parts I and IV.
- **CKAD** — application development. Parts II and III.
- **CKS** — security. Assumes CKA; covers Parts V's material.

All three are practical exams against real clusters, which makes them more meaningful than
most certifications.

## Staying current

Kubernetes releases three times a year and each release deprecates something. Several claims
in this book — the `CrashLoopBackOff`/`Error` reporting, OOM `reason` fields, Gateway API
maturity, in-place pod resizing — are version-dependent and may change.

- **Release notes** — <https://kubernetes.io/docs/setup/release/notes/>. Read the deprecations
  section at minimum.
- **Last Week in Kubernetes Development** — <https://lwkd.info/>. Concise and technical.
- **KubeCon talks** on YouTube — the deep-dive and post-mortem talks especially.
- **CNCF landscape** — <https://landscape.cncf.io/>. A map, not a shortlist (Ch 31).

## Verifying claims yourself

The habit worth keeping from this book: **measure rather than repeat.** Every number here came
from running a command against a real three-node cluster, and several results contradicted
what the documentation and the folklore say — a crash loop that reported `Error` instead of
`CrashLoopBackOff`, an OOM kill that reported `Error` instead of `OOMKilled`, an Ingress
failure that was really a scheduling problem, and eviction thresholds that kind disables
entirely.

A disposable cluster costs a minute:

```bash
kind create cluster
```

When you read a claim about Kubernetes — including one in this book — check it against your
own version. The answer will be authoritative for the cluster you actually run, which is the
one that matters.

---

[Back to contents](../README.md) · Previous: [Appendix B — Glossary](b-glossary.md)

# Chapter 24 — Admission control and supply chain

Admission control is the API server's last checkpoint: after authentication and authorisation,
before persistence. It is where "no pod may run as root", "every workload must have resource
limits", and "only signed images from our registry" become enforceable rather than aspirational.

## The request path

```
request → authentication → authorization → MUTATING admission
        → schema validation → VALIDATING admission → etcd
```

Two things follow from that ordering.

**Mutating runs before validating.** A mutating webhook can add a sidecar, inject default
resource limits, or set a securityContext, and the validating stage then checks the *mutated*
object. That is how service meshes inject proxies and how policy engines can both fix and
reject.

**Both run before anything is stored.** A rejected request never reaches etcd, so admission is
a genuine gate rather than a cleanup process.

Built-in controllers handle a lot already — `NamespaceLifecycle`, `LimitRanger`,
`ResourceQuota`, `ServiceAccount`, `PodSecurity` (Chapter 23), `DefaultStorageClass`. What you
add on top is for policy the built-ins cannot express.

## Policy engines

Writing raw admission webhooks means running an HTTPS server, managing its certificates, and
being in the critical path of every API write. Use a policy engine instead.

**Kyverno** — policies are Kubernetes resources in YAML, no new language:

```yaml
apiVersion: kyverno.io/v1
kind: ClusterPolicy
metadata:
  name: require-resource-limits
spec:
  validationFailureAction: Enforce
  rules:
    - name: check-limits
      match:
        any:
          - resources:
              kinds: ["Pod"]
      validate:
        message: "Every container must set memory and CPU limits."
        pattern:
          spec:
            containers:
              - resources:
                  limits:
                    memory: "?*"
                    cpu: "?*"
```

Kyverno also **mutates** and **generates** — inject default labels, or create a NetworkPolicy
and a ResourceQuota automatically whenever a namespace appears. That generation capability is
genuinely useful for multi-tenancy (Chapter 28).

**OPA Gatekeeper** — policies in Rego, more expressive and a steeper learning curve. Better
when policies need real logic; heavier for simple rules.

**Validating Admission Policy** — built into Kubernetes (stable since v1.30), using CEL
expressions, **with no webhook to run**:

```yaml
apiVersion: admissionregistration.k8s.io/v1
kind: ValidatingAdmissionPolicy
metadata:
  name: require-limits
spec:
  failurePolicy: Fail
  matchConstraints:
    resourceRules:
      - apiGroups: ["apps"]
        apiVersions: ["v1"]
        operations: ["CREATE", "UPDATE"]
        resources: ["deployments"]
  validations:
    - expression: "object.spec.template.spec.containers.all(c, has(c.resources.limits))"
      message: "All containers must have resource limits."
```

This is the option to reach for first now. No extra component, no certificate rotation, no
availability risk, and it covers a large fraction of what people install Gatekeeper for.
Complex policies still want Kyverno or Gatekeeper.

## Webhook failure modes

If you do run webhooks, understand the risk you are taking on.

```yaml
failurePolicy: Fail      # reject requests when the webhook is unreachable
failurePolicy: Ignore    # allow them through
```

`Fail` is the secure choice and it makes your webhook a **hard dependency of the API server**.
A webhook that is down with `failurePolicy: Fail` and a broad `matchConstraints` can block all
pod creation cluster-wide — including the pods that would restore the webhook. That is a
genuine, recurring way to brick a cluster.

Protections, all of which are worth applying:

```yaml
namespaceSelector:
  matchExpressions:
    - key: kubernetes.io/metadata.name
      operator: NotIn
      values: ["kube-system"]
timeoutSeconds: 5
```

- **Exclude `kube-system`** so recovery is always possible.
- **Short timeouts** — the default is 10 s and it is added to every matching API call.
- **Scope narrowly** — match only the resources and operations you actually care about.
- **Run the webhook itself highly available**, with a PDB.

## Supply chain

The Docker book covered building trustworthy images: SBOMs, scanning, provenance and signing.
Kubernetes is where you *enforce* that only such images run.

### Restrict registries

```yaml
apiVersion: kyverno.io/v1
kind: ClusterPolicy
metadata:
  name: restrict-registries
spec:
  validationFailureAction: Enforce
  rules:
    - name: allowed-registries
      match:
        any:
          - resources:
              kinds: ["Pod"]
      validate:
        message: "Images must come from ghcr.io/our-org/."
        pattern:
          spec:
            containers:
              - image: "ghcr.io/our-org/*"
```

Simple and effective — it blocks the typosquatting and random-Docker-Hub-image problems in one
rule.

### Require digests, not tags

```yaml
      validate:
        message: "Images must be referenced by digest."
        pattern:
          spec:
            containers:
              - image: "*@sha256:*"
```

This is the enforcement half of the Docker book's tagging discipline. A tag is mutable; a
digest is not. Requiring digests at admission means the image that passed your pipeline is the
image that runs.

### Verify signatures

```yaml
apiVersion: kyverno.io/v1
kind: ClusterPolicy
metadata:
  name: verify-signatures
spec:
  validationFailureAction: Enforce
  rules:
    - name: verify
      match:
        any:
          - resources:
              kinds: ["Pod"]
      verifyImages:
        - imageReferences: ["ghcr.io/our-org/*"]
          attestors:
            - entries:
                - keyless:
                    subject: "https://github.com/our-org/*"
                    issuer: "https://token.actions.githubusercontent.com"
```

This is what makes signing meaningful. The Docker book's point stands: **signing without
verification is ceremony.** Admission control is where verification happens, and until you
have this policy, `cosign sign` in your pipeline achieves nothing.

Sigstore Policy Controller and Connaisseur do the same job if you prefer a dedicated tool.

### Scan continuously, not just at build

An image that was clean when built accumulates CVEs. Scan what is *running*:

```bash
trivy k8s --report summary cluster
```

Operators like Trivy Operator scan workloads continuously and publish results as custom
resources you can alert on. This closes the gap the Docker book identified: a weekly rebuild
handles images you own; continuous scanning of running workloads catches everything else,
including third-party images.

## A layered policy set

A defensible starting point, roughly in order of value:

1. **Pod Security Standards** — `restricted` where possible, `baseline` elsewhere
   (Chapter 23).
2. **Require resource requests and limits** — otherwise BestEffort pods and no scheduling
   accounting (Chapter 8).
3. **Restrict registries** to your own plus an explicit allowlist.
4. **Require digests** for production namespaces.
5. **Verify signatures** for your own images.
6. **Require standard labels** (owner, team, cost centre) — dull, and it makes everything else
   attributable.
7. **Block `:latest`**, `hostPath`, host networking, and host ports outside a defined
   allowlist.

Roll each out in audit mode first. A policy engine switched straight to enforce across an
existing cluster will block deployments during an incident, and the resulting distrust sets
the whole effort back further than the delay would have.

## Try it

Kyverno and Gatekeeper need installing; the built-in policy does not. Create a
ValidatingAdmissionPolicy requiring resource limits on Deployments:

```bash
kubectl apply -f examples/manifests/24-validating-policy.yaml
```

Now try a Deployment with no limits:

```bash
kubectl create deployment nolimits --image=busybox:1.37 -- sleep 300
```

It is rejected at admission, by the API server, with your message — no webhook involved.

Confirm a compliant one is accepted:

```bash
kubectl apply -f examples/manifests/24-with-limits.yaml && kubectl get deploy withlimits
```

Inspect what admission plugins your API server runs:

```bash
kubectl get --raw='/livez?verbose' | grep -i admission
```

And see the webhooks currently registered — on a fresh cluster this is where the ingress
controller's admission webhook shows up:

```bash
kubectl get validatingwebhookconfigurations,mutatingwebhookconfigurations
```

Check their failure policies, because this is the cluster-bricking risk:

```bash
kubectl get validatingwebhookconfigurations -o custom-columns='NAME:.metadata.name,POLICY:.webhooks[*].failurePolicy,TIMEOUT:.webhooks[*].timeoutSeconds'
```

Clean up:

```bash
kubectl delete -f examples/manifests/24-validating-policy.yaml -f examples/manifests/24-with-limits.yaml --ignore-not-found
```

## Takeaways

- Admission runs **after** authz and **before** etcd. Mutating first, then validating — which
  is how sidecars get injected and defaults applied.
- **Validating Admission Policy (CEL) is built in** and needs no webhook. Try it before
  installing a policy engine.
- Kyverno for YAML-native policy plus mutation and generation; Gatekeeper/Rego for complex
  logic.
- **`failurePolicy: Fail` makes a webhook a hard dependency of the API server.** Exclude
  `kube-system`, use short timeouts, scope narrowly, and run it HA — or you can block all pod
  creation, including the pods that would fix it.
- Enforce supply chain at admission: allowed registries, **digests not tags**, and signature
  verification. **Signing without verification is ceremony.**
- Scan running workloads continuously, not only at build time.
- Roll every policy out in audit mode first.

---

Previous: [Chapter 23 — Security](23-security.md) ·
Next: [Chapter 25 — Scaling and disruption](25-scaling-and-disruption.md)

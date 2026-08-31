# Chapter 23 — Security

Everything the Docker book said about container hardening still applies — non-root, dropped
capabilities, read-only root filesystems, no privileged containers. Kubernetes adds three
things on top: an identity system (ServiceAccounts), an authorisation system (RBAC), and a
mechanism to enforce pod-level hardening cluster-wide (Pod Security Standards).

## The defaults

Two defaults are good and one is not.

**RBAC's default is genuinely restrictive.** What can the default ServiceAccount do?

```bash
kubectl auth can-i --list --as=system:serviceaccount:default:default
```

```
Resources                                       Verbs
selfsubjectreviews.authentication.k8s.io        [create]
selfsubjectaccessreviews.authorization.k8s.io   [create]
clustertrustbundles.certificates.k8s.io         [get list watch]
```

Essentially nothing — it can ask about its own permissions and read public trust bundles.

```bash
kubectl auth can-i get secrets --as=system:serviceaccount:default:default
```

```
no
```

That is the right default, and it means an application that never talks to the API server is
not exposed by RBAC at all.

**But the token is mounted anyway.** Measured on a `pingd` pod:

```bash
kubectl exec POD -- ls /var/run/secrets/kubernetes.io/serviceaccount/
```

```
ca.crt  namespace  token
```

Every pod gets a credential it almost certainly does not need. An attacker with code execution
gets a valid API token for free. Turn it off unless the workload uses the API:

```yaml
spec:
  automountServiceAccountToken: false
```

Or on the ServiceAccount, which covers everything using it:

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: pingd
automountServiceAccountToken: false
```

This is the cheapest security improvement available in Kubernetes, and it is almost never set.

## ServiceAccounts

Every pod runs as a ServiceAccount — `default` in its namespace if you do not specify one.
Give each workload its own:

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: pingd
automountServiceAccountToken: false
---
apiVersion: apps/v1
kind: Deployment
spec:
  template:
    spec:
      serviceAccountName: pingd
```

Two reasons beyond least privilege: the audit log shows *which workload* made a call, and you
can bind cloud IAM to a specific ServiceAccount (IRSA on EKS, Workload Identity on GKE) rather
than to the node — which is what stops every pod on a node sharing the node's cloud
permissions.

Modern tokens are **short-lived and audience-bound**, projected into the pod and rotated
automatically. The long-lived `Secret`-based tokens of older Kubernetes are no longer created
by default; if you find one, treat it as a credential to rotate.

## RBAC

Four object types, and the pattern is regular:

- **Role** — permissions **within one namespace**.
- **ClusterRole** — permissions cluster-wide, or on cluster-scoped resources.
- **RoleBinding** — grants a Role *or* a ClusterRole, **within one namespace**.
- **ClusterRoleBinding** — grants a ClusterRole cluster-wide.

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  namespace: default
  name: pingd-reader
rules:
  - apiGroups: [""]
    resources: ["configmaps"]
    resourceNames: ["pingd-config"]      # narrow to one object
    verbs: ["get", "watch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  namespace: default
  name: pingd-reader
subjects:
  - kind: ServiceAccount
    name: pingd
    namespace: default
roleRef:
  kind: Role
  name: pingd-reader
  apiGroup: rbac.authorization.k8s.io
```

The combination worth knowing: **a RoleBinding referencing a ClusterRole grants that
ClusterRole's permissions only inside the binding's namespace.** That is how you reuse the
built-in `view`/`edit`/`admin` ClusterRoles per namespace instead of writing your own:

```bash
kubectl create rolebinding team-a-edit --clusterrole=edit --group=team-a -n team-a
```

RBAC is **purely additive** — there are no deny rules. Permissions are the union of every
binding that applies. You cannot subtract, only avoid granting.

### The permissions that are really cluster-admin

Several innocuous-looking grants are equivalent to full control. Know them before you write a
Role:

- **`secrets: list`** in a namespace reads *every* secret in it. `get` with `resourceNames` is
  narrower; `list` is not restrictable by name.
- **`pods/exec`, `pods/attach`, `pods/portforward`, `pods/ephemeralcontainers`** — run code
  inside any pod, therefore assume its identity and read its secrets. Separate permissions;
  granting `exec` does not grant `debug`.
- **`create pods`** — create a pod with `hostPath: /`, or with another ServiceAccount, and you
  own the node. Pod-creating permission is node-level power unless constrained by Pod Security
  Standards.
- **`escalate` / `bind`** — create roles more powerful than your own. Never grant these.
- **`impersonate`** — become anyone.
- **`nodes/proxy`** — reach the kubelet API directly, bypassing the API server's authorisation.

Audit what you have:

```bash
kubectl get clusterrolebindings -o json | python3 -c "
import json,sys
for b in json.load(sys.stdin)['items']:
    if b['roleRef']['name'] == 'cluster-admin':
        print(b['metadata']['name'], '->', [s.get('name') for s in b.get('subjects') or []])
"
```

```bash
kubectl auth can-i --list --as=system:serviceaccount:NS:SA
```

```bash
kubectl auth can-i create pods --as=jane@example.com -n production
```

`kubectl auth can-i` is the fastest way to answer "can this thing do that", and it works for
any subject.

## Pod Security Standards

The successor to PodSecurityPolicy (removed in v1.25). Three profiles, enforced by a built-in
admission controller, applied with **namespace labels**:

```bash
kubectl label namespace prod pod-security.kubernetes.io/enforce=restricted
kubectl label namespace prod pod-security.kubernetes.io/warn=restricted
kubectl label namespace prod pod-security.kubernetes.io/audit=restricted
```

- **`privileged`** — no restrictions.
- **`baseline`** — blocks the obviously dangerous: privileged containers, host namespaces,
  most `hostPath`, adding capabilities beyond a small set.
- **`restricted`** — the hardened profile: non-root, no privilege escalation, all capabilities
  dropped, seccomp `RuntimeDefault`, restricted volume types.

The three modes matter: `warn` and `audit` let you see what *would* break before you
`enforce`. Roll out with `warn` first, always.

Measured — creating a default `busybox` pod in a `restricted` namespace:

```
Error from server (Forbidden): pods "bad" is forbidden:
violates PodSecurity "restricted:latest":
  allowPrivilegeEscalation != false
  unrestricted capabilities (must set securityContext.capabilities.drop=["ALL"])
  runAsNonRoot != true
  seccompProfile (must set securityContext.seccompProfile.type to "RuntimeDefault")
```

Four itemised violations with the exact field for each. This is unusually good error
reporting, and it is effectively a checklist for writing a compliant pod.

A compliant pod — which is what this book's `pingd` Deployment already is:

```yaml
spec:
  securityContext:
    runAsNonRoot: true
    runAsUser: 10001
    fsGroup: 10001
    seccompProfile:
      type: RuntimeDefault
  containers:
    - name: api
      securityContext:
        allowPrivilegeEscalation: false
        readOnlyRootFilesystem: true
        capabilities:
          drop: ["ALL"]
```

**Important limitation**: PSS applies to *pods*, and most pods are created by controllers. A
Deployment whose template violates the policy is **accepted** — measured:

```
deployment.apps/baddep        0/1   0   0
replicaset.apps/baddep-...    1     0   0
```

The Deployment exists with zero pods, and the real error is in the **ReplicaSet's** events:

```
Warning  FailedCreate  replicaset-controller
  Error creating: pods "baddep-..." is forbidden: violates PodSecurity "restricted:latest": ...
```

Helpfully, `enforce` alone also emits a `Warning:` on stderr when you create the Deployment,
so you are not entirely without feedback. Setting `warn` explicitly is still worth doing —
it covers dry-runs and clients that discard warnings — but the common claim that you get *no*
signal until the ReplicaSet fails is not quite right on current versions.

For anything PSS cannot express — required labels, allowed registries, mandatory resource
limits — you need a policy engine (Chapter 24).

## securityContext

The Docker book's hardening list, in Kubernetes form:

```yaml
spec:
  securityContext:              # pod level
    runAsNonRoot: true
    runAsUser: 10001
    runAsGroup: 10001
    fsGroup: 10001              # group ownership of mounted volumes
    seccompProfile:
      type: RuntimeDefault
  containers:
    - name: api
      securityContext:          # container level, overrides pod level
        allowPrivilegeEscalation: false
        readOnlyRootFilesystem: true
        privileged: false
        capabilities:
          drop: ["ALL"]
```

Notes that save time:

- **`runAsNonRoot: true` requires a numeric UID in the image**, because the kubelet cannot
  resolve a username to check it. This is why the Docker book insisted on `USER 10001`.
- **`fsGroup`** sets group ownership on mounted volumes — the fix for "permission denied" on a
  PVC when running non-root.
- `readOnlyRootFilesystem` needs writable paths supplied as `emptyDir` volumes (Chapter 15).
- Container-level settings override pod-level; some fields exist at only one level.

## The four layers of a secure cluster

1. **Authentication** — who you are. Certificates, OIDC, cloud IAM. Kubernetes has no user
   objects; identity comes from outside.
2. **Authorization** — RBAC. Least privilege, per workload.
3. **Admission** — what may be created. PSS plus a policy engine (Chapter 24).
4. **Runtime** — what a running container may do. securityContext, NetworkPolicy (Chapter 14),
   and the kernel-level isolation from the Docker book.

Weakness at any layer undermines the rest. `create pods` without admission control is
cluster-admin; admission control without RBAC is bypassable by editing the policy.

## Audit logging

```yaml
apiVersion: audit.k8s.io/v1
kind: Policy
rules:
  - level: RequestResponse
    resources:
      - group: ""
        resources: ["secrets"]
  - level: Metadata
    resources:
      - group: ""
        resources: ["pods/exec", "pods/attach", "pods/portforward"]
```

Log secret access and every `exec` at minimum. Without audit logging you cannot answer "who
read that" or "who ran what in production", which is precisely the question after an incident.
Managed clusters expose this through their cloud logging; enable it before you need it.

## Try it

Check the default ServiceAccount's permissions:

```bash
kubectl auth can-i --list --as=system:serviceaccount:default:default
```

```bash
kubectl auth can-i get secrets --as=system:serviceaccount:default:default
```

Confirm the token is mounted whether you need it or not:

```bash
POD=$(kubectl get pod -l app=pingd -o jsonpath='{.items[0].metadata.name}') && kubectl exec $POD -- ls /var/run/secrets/kubernetes.io/serviceaccount/
```

Now see Pod Security Standards reject a non-compliant pod:

```bash
kubectl create namespace pss-test && kubectl label namespace pss-test pod-security.kubernetes.io/enforce=restricted
```

```bash
kubectl run bad -n pss-test --image=busybox:1.37 --restart=Never --command -- sleep 60
```

Read the four itemised violations. Now a compliant pod in the same namespace:

```bash
kubectl apply -n pss-test -f examples/manifests/23-compliant-pod.yaml && kubectl get pod -n pss-test good
```

And confirm the *controller* case — a violating Deployment is accepted, then fails silently:

```bash
kubectl create deployment baddep -n pss-test --image=busybox:1.37 -- sleep 300
```

```bash
sleep 8 && kubectl get deploy,rs,pods -n pss-test | head -6
```

```bash
kubectl describe rs -n pss-test -l app=baddep | grep -A5 Events | tail -4
```

The Deployment exists with zero pods, and the reason is only in the ReplicaSet's events.

Clean up:

```bash
kubectl delete namespace pss-test
```

## Takeaways

- RBAC's default is restrictive — but **the ServiceAccount token is mounted into every pod
  anyway**. Set `automountServiceAccountToken: false` unless the workload uses the API.
- Give each workload its own ServiceAccount: least privilege, audit attribution, and cloud IAM
  binding.
- A **RoleBinding referencing a ClusterRole** grants it within one namespace — use it to reuse
  `view`/`edit`/`admin`.
- RBAC is additive; there are no deny rules.
- `secrets: list`, `pods/exec`, `create pods`, `escalate`, `bind`, `impersonate` and
  `nodes/proxy` are effectively cluster-admin. Audit them.
- **Pod Security Standards** enforce hardening by namespace label. Use `warn` before
  `enforce`. Violations are itemised with the exact fields.
- PSS applies to pods, so a violating **Deployment is accepted** and fails at the ReplicaSet —
  enable `warn` for feedback at apply time.
- `runAsNonRoot` requires a numeric UID in the image; `fsGroup` fixes volume permissions.
- Four layers: authentication, authorization, admission, runtime. Weakness anywhere undermines
  the rest.

---

Previous: [Chapter 22 — Cookbook](22-cookbook.md) ·
Next: [Chapter 24 — Admission control and supply chain](24-admission-and-supply-chain.md)

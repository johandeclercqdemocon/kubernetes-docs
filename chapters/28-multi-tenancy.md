# Chapter 28 — Multi-tenancy

Several teams, one cluster. It is the usual reason to adopt Kubernetes and the source of its
sharpest limitation: **namespaces are an organisational boundary, not a security one**, and a
great deal of multi-tenancy design is working around that.

## What a namespace gives you

- **Name scoping** — two teams can both have a `pingd` Service.
- **An RBAC target** — permissions granted per namespace (Chapter 23).
- **A quota target** — ResourceQuota and LimitRange.
- **A policy target** — NetworkPolicy, Pod Security Standards.

That is genuinely a lot, and it covers most *cooperative* multi-tenancy: teams within one
organisation who are not trying to attack each other.

## What it does not give you

**Kernel isolation.** Pods from different namespaces run on the same nodes, sharing a kernel.
A container escape crosses every namespace on that node. This is the Docker book's Chapter 1
point, unchanged.

**Network isolation.** Every pod can reach every other pod in every namespace **by default**.
Namespaces do nothing about this — you need NetworkPolicy (Chapter 14), and you need it in
every namespace.

**Resource isolation.** Without quotas, one namespace can consume the whole cluster. Even with
quotas, noisy neighbours share node CPU, memory bandwidth, disk and network.

**Control plane isolation.** All tenants share one API server and one etcd. A tenant creating
objects in a hot loop degrades the cluster for everyone.

**Cluster-scoped resources.** CRDs, ClusterRoles, StorageClasses, PriorityClasses,
webhooks — all global. One tenant installing an operator affects everyone.

So the honest position: **namespaces are appropriate for teams that trust each other. Hostile
or untrusted tenants need separate clusters**, or virtual control planes (vCluster, Capsule),
or sandboxed runtimes (gVisor, Kata) — and usually a combination.

## ResourceQuota

```yaml
apiVersion: v1
kind: ResourceQuota
metadata:
  name: team-a
  namespace: team-a
spec:
  hard:
    requests.cpu: "10"
    requests.memory: 20Gi
    limits.cpu: "20"
    limits.memory: 40Gi
    persistentvolumeclaims: "10"
    requests.storage: 100Gi
    pods: "50"
    services.loadbalancers: "2"
    count/deployments.apps: "20"
```

Quota on `services.loadbalancers` is worth setting: each one is a cloud load balancer with a
bill (Chapter 11), and it is the cheapest way for a team to accidentally spend money.

**The interaction that breaks existing workloads**: once a quota on `requests.cpu` exists in a
namespace, **every pod must specify that request** or creation is rejected. Adding a quota to a
namespace full of pods that never set requests will block all future deployments there.

Always pair it with a LimitRange so existing manifests keep working:

```yaml
apiVersion: v1
kind: LimitRange
metadata:
  name: defaults
  namespace: team-a
spec:
  limits:
    - type: Container
      default:                # applied as limits if unset
        cpu: 500m
        memory: 512Mi
      defaultRequest:         # applied as requests if unset
        cpu: 50m
        memory: 128Mi
      max:
        cpu: "4"
        memory: 8Gi
      min:
        cpu: 10m
        memory: 16Mi
```

This also eliminates BestEffort pods across the namespace, which is Chapter 20's eviction
ranking problem solved by default.

Check usage:

```bash
kubectl describe resourcequota -n team-a
```

Quota is also **scoped**, which is useful for separating long-running from batch workloads:

```yaml
spec:
  scopeSelector:
    matchExpressions:
      - operator: In
        scopeName: PriorityClass
        values: ["high-priority"]
```

## A namespace template

What every tenant namespace should get, ideally generated automatically (Kyverno's generation
rules, from Chapter 24, do this well):

1. **ResourceQuota** — capacity ceiling.
2. **LimitRange** — defaults so quota does not break things.
3. **NetworkPolicy** — default-deny ingress, plus DNS egress.
4. **Pod Security Standards labels** — `enforce=baseline` or `restricted`.
5. **RBAC** — a RoleBinding granting the team `edit` in their namespace only.
6. **Ownership labels** — team, cost centre, contact.

```bash
kubectl create rolebinding team-a-edit --clusterrole=edit --group=team-a -n team-a
```

Note `edit` rather than `admin`: `admin` includes the ability to create RoleBindings, which
lets a tenant grant themselves anything else the cluster permits within that namespace.

## What tenants must not have

- **`cluster-admin`**, obviously.
- **ClusterRole creation** or `escalate`/`bind` — self-escalation.
- **CRD creation** — cluster-global, and CRDs change API behaviour for everyone.
- **Node access** — `nodes/proxy`, or the ability to create privileged pods, `hostPath`
  mounts, or host networking. Pod Security Standards is what enforces this.
- **Webhook registration** — a tenant webhook with `failurePolicy: Fail` can break the cluster
  (Chapter 24).

The pattern to internalise: **`create pods` is node-level power unless admission control
constrains it.** RBAC alone does not make a tenant safe.

## Stronger isolation

When cooperative multi-tenancy is not enough, in increasing order of separation:

**Node isolation.** Dedicate nodes per tenant with taints and node affinity. Removes the shared
kernel problem between tenants, at the cost of poorer bin-packing.

```bash
kubectl taint nodes NODE tenant=team-a:NoSchedule
kubectl label nodes NODE tenant=team-a
```

**Sandboxed runtimes.** gVisor or Kata Containers via RuntimeClass, so a container escape does
not reach the host kernel:

```yaml
apiVersion: node.k8s.io/v1
kind: RuntimeClass
metadata:
  name: gvisor
handler: runsc
---
spec:
  runtimeClassName: gvisor
```

This is the real answer for running untrusted code — customer-submitted workloads, CI for
arbitrary pull requests, anything you did not write.

**Virtual clusters.** vCluster runs a tenant control plane (API server, scheduler) inside a
namespace of a host cluster. Tenants get their own CRDs, ClusterRoles and API server, while
pods actually run on the host's nodes. A genuinely good middle ground: much stronger isolation
than namespaces, far cheaper than separate clusters.

**Separate clusters.** Complete isolation, complete duplication of cost and operational
effort. The right answer for hostile tenants, hard regulatory boundaries, or when blast radius
must be absolute.

## Cost attribution

Multi-tenancy raises "who is spending what". Requests are the basis — a namespace's cost is
roughly its share of node cost weighted by requests, which is why over-requesting is expensive
even when nothing is used.

OpenCost and Kubecost do this properly (Chapter 26). Enforce ownership labels by policy so the
attribution has something to attribute to:

```yaml
      validate:
        message: "All workloads must carry a team label."
        pattern:
          metadata:
            labels:
              team: "?*"
```

## Try it

Create a tenant namespace with the full template:

```bash
kubectl apply -f examples/manifests/28-tenant.yaml
```

```bash
kubectl describe resourcequota -n team-a
```

See the LimitRange supply defaults to a pod that specifies nothing:

```bash
kubectl run defaulted -n team-a --image=busybox:1.37 --restart=Never --command -- sleep 300
```

```bash
sleep 5 && kubectl get pod defaulted -n team-a -o jsonpath='requests={.spec.containers[0].resources.requests} limits={.spec.containers[0].resources.limits}{"\n"}'
```

Requests and limits it never asked for, and therefore **not** BestEffort:

```bash
kubectl get pod defaulted -n team-a -o jsonpath='qos={.status.qosClass}{"\n"}'
```

Now hit the quota. Ask for more than the namespace permits:

```bash
kubectl run toobig -n team-a --image=busybox:1.37 --restart=Never --overrides='{"spec":{"containers":[{"name":"toobig","image":"busybox:1.37","command":["sleep","300"],"resources":{"requests":{"cpu":"50","memory":"100Gi"},"limits":{"cpu":"50","memory":"100Gi"}}}]}}'
```

```
Error from server (Forbidden): pods "toobig" is forbidden:
[maximum cpu usage per Container is 2, but limit is 50,
 maximum memory usage per Container is 4Gi, but limit is 100Gi]
```

Rejected at admission with exact numbers — and note *which* object caught it: the
**LimitRange's `max`**, not the ResourceQuota, because a per-container ceiling is checked
before the namespace total. The two work together, and reading the message tells you which
limit you hit.

Check that a tenant cannot escalate:

```bash
kubectl auth can-i create clusterrolebindings --as=system:serviceaccount:team-a:default
kubectl auth can-i get secrets -n default --as=system:serviceaccount:team-a:default
```

Clean up:

```bash
kubectl delete namespace team-a
```

## Takeaways

- Namespaces scope names, RBAC, quotas and policy. They provide **no kernel, network, resource
  or control-plane isolation**.
- Suitable for **cooperative** tenants. Hostile or untrusted workloads need node isolation,
  sandboxed runtimes, virtual clusters or separate clusters.
- **A ResourceQuota forces every pod to specify the quotaed resource.** Always pair it with a
  LimitRange, which also eliminates BestEffort pods.
- Quota `services.loadbalancers` — each one costs money.
- Grant tenants `edit`, not `admin`; `admin` lets them create RoleBindings.
- Never grant CRD creation, webhook registration, `escalate`/`bind`, or node access.
- **`create pods` is node-level power unless Pod Security Standards constrains it.**
- vCluster is a strong middle ground between namespaces and separate clusters.
- Attribute cost by requests, and enforce ownership labels so there is something to attribute
  to.

---

Previous: [Chapter 27 — Deployment strategies and GitOps](27-deployment-and-gitops.md) ·
Next: [Chapter 29 — Packaging: Helm and Kustomize](29-helm-and-kustomize.md)

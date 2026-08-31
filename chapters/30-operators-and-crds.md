# Chapter 30 — Operators and CRDs

Chapter 2 described the control loop: observe desired state, observe actual state, close the
gap. Custom resources let you use that machinery for **your own** concepts, and operators are
the controllers that do it.

This is the mechanism behind most of the Kubernetes ecosystem — cert-manager, Prometheus
Operator, database operators, Argo CD. It is also the point at which people start building
things they should not.

## Custom Resource Definitions

A CRD teaches the API server a new type:

```yaml
apiVersion: apiextensions.k8s.io/v1
kind: CustomResourceDefinition
metadata:
  name: pingdservices.example.com
spec:
  group: example.com
  names:
    kind: PingdService
    plural: pingdservices
    singular: pingdservice
    shortNames: ["pds"]
  scope: Namespaced
  versions:
    - name: v1alpha1
      served: true
      storage: true
      subresources:
        status: {}
      schema:
        openAPIV3Schema:
          type: object
          properties:
            spec:
              type: object
              required: ["replicas"]
              properties:
                replicas:
                  type: integer
                  minimum: 1
                  maximum: 100
                version:
                  type: string
            status:
              type: object
              properties:
                readyReplicas: {type: integer}
      additionalPrinterColumns:
        - name: Replicas
          type: integer
          jsonPath: .spec.replicas
```

Once applied, that type behaves like any built-in:

```bash
kubectl get pingdservices
kubectl explain pingdservice.spec
kubectl get pds -o yaml
```

RBAC applies, admission applies, `kubectl explain` works from your schema, and it is stored in
etcd. **You have extended the API, not built something alongside it** — that is the whole
appeal.

Details that matter:

- **A schema is effectively mandatory.** Without validation you get a typed object with
  untyped contents, and errors surface at runtime instead of at apply.
- **`subresources: status: {}`** separates spec from status, so a controller updating status
  cannot accidentally modify spec, and RBAC can be granted separately. Always enable it.
- **`additionalPrinterColumns`** makes `kubectl get` readable — cheap and consistently
  appreciated.
- **Versioning is real work.** Once `v1alpha1` has users, moving to `v1beta1` needs a
  conversion strategy, possibly a conversion webhook. Design the schema as though changing it
  is expensive, because it is.
- **CRDs are cluster-scoped.** Installing one affects every namespace, which is why tenants
  must not be allowed to create them (Chapter 28).

## What an operator is

A controller that reconciles your custom resource. The loop is exactly Chapter 2's:

```
watch PingdService objects
  → read spec (desired)
  → read actual (Deployments, Services, external systems)
  → take one step to close the gap
  → update status
  → repeat
```

The genuine value is **encoding operational knowledge as software**. A database operator does
not merely create a StatefulSet; it handles failover, backup scheduling, point-in-time
recovery, version upgrades, connection routing and replica scaling. That accumulated knowledge
*is* the product, and it is why Chapter 15 recommended an operator over a hand-written
StatefulSet for databases.

## Building one

**Kubebuilder / controller-runtime (Go)** — the reference approach, used by most serious
operators:

```go
func (r *PingdServiceReconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
    var svc examplev1alpha1.PingdService
    if err := r.Get(ctx, req.NamespacedName, &svc); err != nil {
        return ctrl.Result{}, client.IgnoreNotFound(err)
    }

    desired := buildDeployment(&svc)
    if err := ctrl.SetControllerReference(&svc, desired, r.Scheme); err != nil {
        return ctrl.Result{}, err
    }
    if err := r.Patch(ctx, desired, client.Apply, client.ForceOwnership,
        client.FieldOwner("pingd-operator")); err != nil {
        return ctrl.Result{}, err
    }

    svc.Status.ReadyReplicas = observed.Status.ReadyReplicas
    return ctrl.Result{}, r.Status().Update(ctx, &svc)
}
```

**Operator SDK** wraps Kubebuilder and adds Ansible and Helm-based operators — useful when the
logic is "install this chart with these values".

**Metacontroller / kopf (Python) / shell-operator** — lighter options when the logic is
simple.

### The rules that make a reconciler correct

These are where most bespoke operators go wrong:

**Be idempotent.** `Reconcile` will be called repeatedly for the same object, on a resync
timer, after restarts, and on unrelated events. Running it twice must be identical to running
it once.

**Be level-triggered, not edge-triggered.** Read current state; never assume you saw the
previous event. Chapter 2's point, and the thing that makes controllers robust.

**Set `ownerReferences`.** This is what makes garbage collection delete your children when the
parent goes (Chapter 2). Forgetting it leaks resources permanently.

**Report through `status.conditions`.** Use the standard shape — `type`, `status`, `reason`,
`message`, `observedGeneration`. Every debugging tool and every human knows how to read it.

**Never block.** A `Reconcile` that waits on a slow external call stalls the queue for every
object. Return and requeue instead:

```go
return ctrl.Result{RequeueAfter: 30 * time.Second}, nil
```

**Use finalizers only when you must**, and make them robust. A finalizer whose controller is
gone blocks deletion forever, which is Chapter 21's stuck-namespace problem.

**Handle conflicts.** Optimistic concurrency (Chapter 2) means updates fail if something else
wrote first. Retry on conflict; do not treat it as an error.

## Should you build one?

Usually not. The honest test:

**Build an operator when:**

- You are operating stateful, complex software where the operational knowledge is real —
  failover, backups, upgrades.
- You need to reconcile external systems (cloud resources, DNS, certificates) with cluster
  state.
- The same runbook is executed often enough that automating it saves genuine time.
- You are distributing software others will run.

**Do not build one when:**

- A Helm chart or Kustomize overlay would do. This covers most cases.
- You want to abstract a Deployment behind a friendlier name. You have added a component,
  a schema, RBAC, a release process and a failure mode, and saved twenty lines of YAML.
- Nobody will maintain it. An unmaintained operator is worse than no operator: it holds
  finalizers, blocks upgrades, and eventually breaks against a newer API.

An operator is a **distributed system you now own**. It runs continuously, has privileged
access, and can damage the cluster if it misbehaves — a buggy reconcile loop creating objects
in a hot cycle is an effective denial of service against your own API server, and it happens.

Before building, check [OperatorHub](https://operatorhub.io/) and the CNCF landscape. The
mature operators for Postgres, Kafka, Redis, Elasticsearch and certificates represent many
person-years each.

## Consuming operators

Which is what most people should do.

**Evaluate before adopting**: is it maintained, does it have a CRD versioning story, what RBAC
does it demand (many want cluster-admin), how does it upgrade, and what happens if it stops
running?

That last question matters most. A well-built operator's absence should be *inert* — existing
workloads keep running, and only changes stop. An operator whose absence breaks running
workloads is a single point of failure you have installed voluntarily.

**Watch out for CRD upgrades.** As Chapter 29 noted, Helm does **not** upgrade CRDs on
`helm upgrade`. Operator upgrades frequently require applying new CRDs manually first, and
skipping it produces confusing failures where new fields are silently dropped.

**Uninstalling is harder than installing.** Removing an operator leaves its CRDs, and deleting
a CRD **deletes every object of that type** — which for a database operator means deleting
your databases. Read the uninstall documentation before you need it.

## Try it

Create a CRD and use it as a first-class API object — no controller needed to see the
machinery:

```bash
kubectl apply -f examples/manifests/30-crd.yaml
```

```bash
kubectl api-resources | grep example.com
```

Your type is now in the API. It has documentation generated from your schema:

```bash
kubectl explain pingdservice.spec
```

Create one:

```bash
kubectl apply -f examples/manifests/30-cr.yaml && kubectl get pds
```

The custom printer columns show up in `kubectl get`. Now watch the schema reject invalid
input — this is validation you got for free:

```bash
kubectl apply -f - <<'EOF'
apiVersion: example.com/v1alpha1
kind: PingdService
metadata:
  name: invalid
spec:
  replicas: 500
EOF
```

Rejected, because the schema caps `replicas` at 100. And a missing required field:

```bash
kubectl apply -f - <<'EOF'
apiVersion: example.com/v1alpha1
kind: PingdService
metadata:
  name: incomplete
spec:
  version: "1.0"
EOF
```

Nothing is running a controller, so nothing happens beyond storage — which is precisely the
point: **a CRD is an API, and an operator is what gives it behaviour.**

Clean up — **⚠️ deleting a CRD deletes every object of that type**:

```bash
kubectl delete -f examples/manifests/30-crd.yaml
```

## Takeaways

- A CRD extends the API server: RBAC, admission, `kubectl explain` and etcd storage all apply
  to your type.
- Always define a **schema**, enable the **status subresource**, and add
  `additionalPrinterColumns`. Treat versioning as expensive from day one.
- CRDs are **cluster-scoped** — never let tenants create them.
- An operator is a controller for your CRD. The value is **encoded operational knowledge**,
  not abstraction.
- Reconcilers must be **idempotent, level-triggered, non-blocking**, set `ownerReferences`,
  report via `status.conditions`, and retry on conflict.
- **Most teams should not build one.** If Helm or Kustomize would do, use those.
- An operator is a privileged distributed system you now own and must maintain.
- When adopting one, ask what happens if it stops — its absence should be inert.
- **Helm does not upgrade CRDs**, and **deleting a CRD deletes all its objects**.

---

Previous: [Chapter 29 — Packaging: Helm and Kustomize](29-helm-and-kustomize.md) ·
Next: [Chapter 31 — The ecosystem](31-ecosystem.md)

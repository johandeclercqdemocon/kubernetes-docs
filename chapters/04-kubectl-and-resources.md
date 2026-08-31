# Chapter 4 — kubectl and the resource model

Kubernetes is an API with a uniform object model. Once you know the shape of that model,
resources you have never met behave predictably — which is what makes the ecosystem's
hundreds of custom resource types tractable rather than overwhelming.

## Everything is an object

Same five top-level fields, every time:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata: {}
spec: {}
status: {}
```

`apiVersion` is `group/version`, except for the original core resources (Pod, Service,
ConfigMap, Secret, Node, Namespace), which are in the empty group and just say `v1`. That
inconsistency is historical and permanent.

On this cluster:

```bash
kubectl api-resources --no-headers | wc -l        # 71 types
kubectl api-versions | wc -l                      # 23 group/versions
```

Of the 71 types, **34 are namespaced and 37 are cluster-scoped**:

```bash
kubectl api-resources --namespaced=true --no-headers | wc -l    # 34
kubectl api-resources --namespaced=false --no-headers | wc -l   # 37
```

Cluster-scoped means it exists once for the whole cluster — Nodes, Namespaces,
PersistentVolumes, StorageClasses, ClusterRoles, CustomResourceDefinitions. Attempting
`kubectl get nodes -n default` produces a warning, because the namespace is meaningless:

```
Warning: resource 'nodes' is not namespace scoped
```

Knowing which is which matters for RBAC (Chapter 23): a Role grants access within one
namespace and simply *cannot* grant access to a cluster-scoped resource, no matter how you
write it. You need a ClusterRole.

## Discovering the API

Three commands worth knowing before anything else, because they replace searching the web
for field names.

```bash
kubectl api-resources
```

Every type, its short name, its API group, and whether it is namespaced. The `SHORTNAMES`
column is where `po`, `svc`, `deploy`, `rs`, `ns`, `cm`, `sa`, `pvc` come from.

```bash
kubectl explain deployment.spec.strategy
```

```
GROUP:      apps
KIND:       Deployment
VERSION:    v1

FIELD: strategy <DeploymentStrategy>
```

Documentation for any field of any type, generated from the API schema — including custom
resources you installed five minutes ago. `--recursive` prints the whole subtree. This is
the authoritative answer to "what fields does this thing have", and it is always correct for
*your* cluster version, which a web search is not.

```bash
kubectl explain deployment.spec --recursive | head -40
```

## Namespaces

A namespace is a name-scoping mechanism with policy attachment points. It is **not** a
security boundary on its own (Chapter 28), and it does not isolate the network unless you
add NetworkPolicy (Chapter 14).

```bash
kubectl get namespaces
```

```bash
kubectl get pods -n kube-system
kubectl get pods --all-namespaces
```

Every cluster has `default` (do not use it for real work), `kube-system` (control plane),
`kube-public` (world-readable cluster info) and `kube-node-lease` (node heartbeats).

Set a default so you stop typing `-n`:

```bash
kubectl config set-context --current --namespace=myapp
```

Namespaces give you: name uniqueness, a target for RBAC, a target for ResourceQuota and
LimitRange, and a scope for NetworkPolicy. That is a lot, but note what is missing — nothing
about node isolation or kernel isolation. Pods from different namespaces run side by side on
the same nodes, sharing a kernel.

## Labels and selectors

This is the mechanism that ties Kubernetes together, and it is worth more attention than it
usually gets.

**Labels** are arbitrary key-value pairs for identification. **Selectors** query them. The
loose coupling between almost all Kubernetes objects is a label selector:

- A Service finds its pods by selector — it knows nothing about your Deployment.
- A ReplicaSet owns pods by selector.
- A NetworkPolicy targets pods by selector.
- A PodDisruptionBudget, an HPA target, a topology spread constraint — all selectors.

```bash
kubectl get pods -l app=pingd
kubectl get pods -l 'app in (pingd, web)'
kubectl get pods -l 'app=pingd,version!=canary'
kubectl get pods -l '!canary'                    # label absent
```

The consequence that bites: **a Service selecting labels no pod has is not an error.** It
exists, has a ClusterIP, and routes to nothing. Traffic to it fails, and nothing anywhere
says "your selector is wrong". Chapter 19 makes checking for this the first step in Service
debugging.

Recommended labels — worth adopting because tooling understands them:

```yaml
metadata:
  labels:
    app.kubernetes.io/name: pingd
    app.kubernetes.io/instance: pingd-prod
    app.kubernetes.io/version: "1.4.2"
    app.kubernetes.io/component: api
    app.kubernetes.io/part-of: platform
```

**Annotations** look similar and are for something else entirely: non-identifying metadata,
not queryable, often large, and frequently written by tools rather than humans — checksums
to force rollouts, ingress controller configuration, `kubectl.kubernetes.io/last-applied-
configuration`. If you want to select on it, it is a label; if you want to attach data to
it, it is an annotation.

## The kubectl verbs

```bash
kubectl get pods                          # list
kubectl get pod NAME -o yaml              # full object
kubectl describe pod NAME                 # human summary + events
kubectl logs NAME                         # container output
kubectl exec -it NAME -- sh               # run a command inside
kubectl apply -f manifest.yaml            # declarative write
kubectl delete -f manifest.yaml
kubectl edit deployment NAME              # fetch, edit, write back
kubectl port-forward svc/pingd 8080:80    # local tunnel
```

Three of those deserve elaboration.

**`describe` versus `get -o yaml`.** `describe` gives you a human summary *plus the object's
recent events*, which `get -o yaml` does not include. For debugging, `describe` is almost
always the right first command — the events are usually the answer.

**`logs` flags you will use constantly:**

```bash
kubectl logs -f --tail=100 --timestamps deploy/pingd
kubectl logs POD -c CONTAINER               # multi-container pod
kubectl logs POD --previous                 # the container that just crashed ← crucial
kubectl logs -l app=pingd --prefix --tail=20    # across all matching pods
kubectl logs POD --since=10m
```

`--previous` is the one people miss. When a container is crash-looping, `kubectl logs` shows
the *current* attempt, which may have produced nothing yet; `--previous` shows the instance
that actually died, with the error in it.

**`-o` output formats:**

```bash
kubectl get pods -o wide                    # + node and pod IP
kubectl get pods -o json | jq '...'
kubectl get pods -o jsonpath='{.items[*].spec.nodeName}'
kubectl get pods -o custom-columns='NAME:.metadata.name,NODE:.spec.nodeName,QOS:.status.qosClass'
kubectl get pods -o name                    # pod/x — feeds other commands
```

`custom-columns` and `jsonpath` are how you stop scrolling. Most examples in this book use
them.

## Events

Events are the cluster's narration of what controllers did, and they are the single most
underused debugging resource.

```bash
kubectl get events --sort-by=.lastTimestamp
```

```bash
kubectl get events --field-selector involvedObject.name=POD_NAME
```

```bash
kubectl events --for pod/POD_NAME --watch
```

Two critical caveats. **Events expire**, by default after one hour — so "there are no events"
on an old problem means nothing. And **`kubectl get events` is not sorted by default**, which
makes it nearly useless without `--sort-by`; that is a long-standing wart, not something you
are doing wrong.

For anything you need to keep, events must be shipped somewhere (Chapter 26).

## Dry runs and diffs

Two commands that prevent a lot of damage:

```bash
kubectl apply -f manifest.yaml --dry-run=server
```

Sends the object to the API server for full validation — schema, admission webhooks,
defaulting — without persisting it. Much stronger than `--dry-run=client`, which only
validates locally and misses everything interesting.

```bash
kubectl diff -f manifest.yaml
```

Shows exactly what applying would change against live state. Run this before any `apply` you
are unsure about; it is the difference between confidence and hoping.

## Server-side apply and field ownership

Modern `kubectl apply` uses **server-side apply**, where the API server tracks which manager
owns which field:

```bash
kubectl get deployment pingd -o yaml | grep -A5 managedFields | head
```

This is why you sometimes get:

```
error: Apply failed with 1 conflict: conflict with "kubectl-edit": .spec.replicas
```

Another manager — an HPA, a GitOps controller, or your own earlier `kubectl edit` — owns
that field. The error is Kubernetes preventing two sources of truth from silently fighting,
which is a feature. `--force-conflicts` takes ownership, and you should understand *what*
you are taking it from before using it.

## Contexts

```bash
kubectl config get-contexts
kubectl config use-context CLUSTER
kubectl config current-context
```

`kubectl` reads `~/.kube/config`, which may hold many clusters. **Running the right command
against the wrong cluster is a genuine and common production incident.** Mitigations worth
adopting: put the current context in your shell prompt (`kube-ps1`, starship), use
`kubectx`/`kubens`, and keep production in a separate kubeconfig file selected explicitly
via `KUBECONFIG`.

## Try it

Explore the API rather than searching the web for it:

```bash
kubectl api-resources | head -20
```

```bash
kubectl explain pod.spec.containers.resources
```

```bash
kubectl explain deployment.spec.strategy.rollingUpdate
```

See the namespaced/cluster-scoped split:

```bash
kubectl api-resources --namespaced=false --no-headers | wc -l; kubectl api-resources --namespaced=true --no-headers | wc -l
```

Watch what `describe` gives you that `get -o yaml` does not:

```bash
kubectl describe pod -l app=pingd | tail -15
```

Those trailing events are the payoff.

Prove that a Service with a wrong selector fails silently — this is Chapter 19's most common
bug, created deliberately:

```bash
kubectl create service clusterip broken --tcp=80:8000 --dry-run=client -o yaml | kubectl apply -f -
```

```bash
kubectl get svc broken
```

It has a ClusterIP and looks completely healthy. Now look for endpoints:

```bash
kubectl get endpointslice -l kubernetes.io/service-name=broken
```

```
NAME           ADDRESSTYPE   PORTS     ENDPOINTS   AGE
broken-hq48j   IPv4          <unset>   <unset>     0s
```

An EndpointSlice *is* created — it is simply empty. That is the tell: `ENDPOINTS` showing
`<unset>` rather than a list of pod IPs. No error, no warning, no event — just a Service
that silently discards traffic.

```bash
kubectl delete svc broken
```

Finally, use `diff` before an apply:

```bash
kubectl diff -f examples/manifests/01-deployment.yaml || true
```

## Takeaways

- Every object has `apiVersion`, `kind`, `metadata`, `spec`, `status`. Learn the shape once
  and every custom resource is familiar.
- `kubectl api-resources` and `kubectl explain` are the authoritative documentation for
  *your* cluster version. Use them instead of searching.
- 71 types on a bare cluster: 34 namespaced, 37 cluster-scoped. The distinction determines
  whether a Role or ClusterRole is even possible.
- Namespaces scope names, RBAC, quotas and policy. They are not a security or network
  boundary by themselves.
- **Label selectors are the coupling between nearly all objects**, and a selector matching
  nothing is never an error — it just silently does nothing.
- `describe` includes events; `get -o yaml` does not. Events expire after an hour and are
  unsorted by default.
- `kubectl logs --previous` shows the container that actually crashed.
- `--dry-run=server` and `kubectl diff` before applying. Server-side apply conflicts mean
  something else owns that field — find out what before forcing.
- Wrong-context accidents are real. Put the context in your prompt.

---

Previous: [Chapter 3 — Cluster anatomy](03-cluster-anatomy.md) ·
Next: [Chapter 5 — Pods](05-pods.md)

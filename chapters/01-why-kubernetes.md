# Chapter 1 — Why Kubernetes

## The problem it actually solves

Containers solved packaging. An image is a reproducible artifact that runs the same
everywhere, which is a genuine advance and the subject of the companion book. What
containers did not solve is everything that happens *after* you have more than one machine
and more than a handful of containers:

- A host dies at 03:00. Which of its containers should run where now, and who decides?
- You want to deploy without downtime. Which containers stop, in what order, and what if
  the new ones are broken?
- Traffic triples on Friday. Who notices, and who starts more replicas?
- Forty services need to find each other, and their addresses change constantly.
- Fifteen teams deploy to shared machines. Who stops one team's runaway job from starving
  everyone else?

Each of these has a manual answer, and the manual answers are scripts. Every organisation
that ran containers at scale before orchestration wrote those scripts, and they all
converged on the same thing: a loop that compares what should be running against what is
running, and fixes the difference.

Kubernetes is that loop, generalised and given an API. That is the whole idea, and
Chapter 2 is devoted to it.

## What you get

Concretely, and stated as capabilities rather than features:

**Declarative desired state.** You describe what should exist. Controllers make it so, and
keep making it so. You do not run deployment steps; you change a record and the system
converges. This is genuinely different from `docker run`, and it takes a while to stop
thinking imperatively.

**Self-healing.** A pod dies, a controller notices and replaces it. A node dies, its pods
are rescheduled elsewhere. Measured on this book's cluster, deleting a pod from a
three-replica Deployment produced a replacement in under six seconds, unprompted.

**Rolling updates and rollbacks** as first-class operations, with health gating so a broken
version stops the rollout rather than replacing everything.

**Service discovery and load balancing** built in. A stable name and virtual IP in front of
a changing set of pods.

**Bin-packing.** The scheduler places workloads on nodes according to their declared
resource needs, which is a genuine efficiency win at any scale beyond a few machines.

**Horizontal autoscaling** on CPU, memory or custom metrics.

**A uniform API for everything**, extensible with your own resource types. This is the
underrated one: it means storage, certificates, DNS records and databases can all be
managed the same way as workloads, which is what the operator pattern (Chapter 30) is
built on.

**An enormous ecosystem.** Whatever you need to do, something exists. This is a real
benefit and, as the next section argues, also a cost.

## What it costs

Honesty here matters more than enthusiasm, because the failure mode of Kubernetes adoption
is not technical — it is a small team spending its engineering capacity on a platform
instead of a product.

**Conceptual surface.** Pods, ReplicaSets, Deployments, Services, EndpointSlices, Ingress,
ConfigMaps, Secrets, ServiceAccounts, Roles, RoleBindings, PersistentVolumes,
PersistentVolumeClaims, StorageClasses, StatefulSets, DaemonSets, Jobs, CronJobs, HPAs,
PDBs, NetworkPolicies, CRDs, admission webhooks. A default cluster exposes **71 API
resource types** before you install anything. You will meet most of them.

**Operational burden.** A cluster is infrastructure. It needs upgrading — control plane and
nodes, on a support window measured in months, not years. It needs monitoring, certificate
rotation, etcd backups, and someone who understands it at 03:00. Managed control planes
(EKS, GKE, AKS) remove a large part of this and not all of it: you still own the nodes,
the workloads, and every decision above the API.

**Debugging has more layers.** In the Docker book, a failing container had four places to
look. Here there are five, and one of them — the control plane — can itself be the problem.
An application that will not start might be failing because of an image, a probe, a
resource request no node can satisfy, an admission webhook, a missing RBAC permission, a
NetworkPolicy, or a taint. Part IV exists because of this.

**Cost.** Control plane fees, nodes that must be over-provisioned for headroom, and the
engineering time. A three-node cluster running one service costs meaningfully more than a
VM running one service.

**The ecosystem is a cost too.** For any problem there are six projects, three of which are
abandoned and two of which are commercial products with an open core. Evaluating them is
work, and every one you adopt is another thing to upgrade.

## When not to use it

The companion book's Chapter 30 laid out the decision; here is the short version from the
other side.

**Do not adopt Kubernetes because you might need to scale.** You will not need to scale in
the way you imagine, and the version of your system that eventually does need to scale will
not be the version you are building now.

**Do not adopt it for one service.** A Deployment, a Service and an Ingress to run one
container is a great deal of apparatus. A managed container service — Cloud Run, ECS
Fargate, Container Apps — gives you rolling deploys, autoscaling, TLS and health checks
without a cluster, and is the right answer far more often than its reputation suggests.

**Do not adopt it to solve a problem you have not had yet.** The good reasons are present
constraints: you already run several hosts and coordinate them with scripts; deploy
downtime is unacceptable; a node failure must not be an outage; many teams need isolated,
self-service deploys.

**Do not adopt it if nobody will own it.** A cluster without a maintainer degrades. Version
skew accumulates, certificates expire, and the first serious incident finds nobody who
understands the system.

The honest summary: Kubernetes is very good at the problems it exists for, and those
problems arrive later than most teams expect.

## What Kubernetes is not

**Not a PaaS.** It does not build your code, manage your database, or give you a `git push`
deploy. Those are things you assemble on top, which is why platform engineering exists as a
discipline.

**Not a way to avoid understanding your system.** It automates decisions you must still be
able to make. A pod that will not schedule is a resource-allocation question, and the
scheduler will not answer it for you.

**Not a security boundary by default.** Pods on a node share a kernel, exactly as
containers do. Namespaces are an *organisational* boundary with optional policy attached,
not a hard multi-tenancy boundary. Chapter 23 covers what to do; Chapter 28 covers the
limits.

**Not portable in the way people mean.** The API is portable; your manifests will run
anywhere. Your *system* depends on storage classes, load balancer implementations, ingress
controllers, node types and cloud IAM, and those differ. "No vendor lock-in" is
overstated — it is better than the alternative, not free.

**Not a replacement for containers.** Everything in the Docker book still applies. Your
images, layers, healthchecks, non-root users, signal handling and logging behaviour carry
over unchanged, and Kubernetes will punish you for getting them wrong more visibly than
Docker did.

## How this book is organised

Part I builds the model: reconciliation, cluster anatomy, and the resource system. Part II
runs workloads. Part III connects and stores. Part IV is debugging, which is where most of
your time will actually go. Part V is what production requires. Part VI is the ecosystem
and the mistakes.

The chapters assume Linux nodes and a recent cluster (v1.30+). Everything was checked
against **v1.37.0**.

## Try it

If you want to follow along, a three-node cluster takes about a minute:

```bash
kind create cluster --config examples/cluster/kind-cluster.yaml
```

```bash
kubectl get nodes
```

```
NAME                    STATUS   ROLES           AGE   VERSION
k8sbook-control-plane   Ready    control-plane   29s   v1.37.0
k8sbook-worker          Ready    <none>          13s   v1.37.0
k8sbook-worker2         Ready    <none>          13s   v1.37.0
```

Now look at the size of the thing you just started. This is every resource type the API
serves, before you install anything at all:

```bash
kubectl api-resources --no-headers | wc -l
```

```
71
```

And the control plane components running as pods on the control-plane node:

```bash
kubectl get pods -n kube-system
```

Note `etcd`, `kube-apiserver`, `kube-controller-manager`, `kube-scheduler`, plus
`kube-proxy` and a CNI plugin on every node, and CoreDNS. Chapter 3 explains what each
does. For now the point is simply that a "minimal" cluster is already a distributed system
with seven or eight moving parts — which is the cost side of this chapter, made concrete.

Finally, watch self-healing, since it is the capability everything else rests on:

```bash
kubectl create deployment recon --image=nginx:alpine --replicas=2
```

```bash
kubectl get pods -l app=recon
```

```bash
kubectl delete pod -l app=recon --field-selector status.phase=Running --wait=false | head -1
```

Within a few seconds a replacement appears with a new name and an age of zero. Nothing
issued a "create a pod" command; a controller noticed a difference and closed it.

Clean up:

```bash
kubectl delete deployment recon
```

## Takeaways

- Kubernetes is a control loop with an API: describe desired state, controllers converge
  towards it continuously.
- The real capabilities are self-healing, rolling updates, service discovery, bin-packing,
  autoscaling, and a uniform extensible API.
- The real costs are conceptual surface (71 API types out of the box), cluster operations,
  five debugging layers instead of four, and money.
- Adopt for a **present constraint**, not anticipated scale. For one service, a managed
  container service is usually better.
- It is not a PaaS, not a hard security boundary, and not as portable as claimed.
- Everything you know about containers still applies — and bad container practice hurts
  more here.

---

Next: [Chapter 2 — Declarative reconciliation](02-reconciliation.md)

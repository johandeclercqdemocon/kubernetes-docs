# Chapter 3 — Cluster anatomy

Chapter 2 described five actors. This chapter says where they live, what each one owns, and
what breaks when each fails — which is the knowledge Part IV depends on.

## The two halves

```
        CONTROL PLANE                          NODES (every one)
  ┌───────────────────────────┐          ┌──────────────────────────┐
  │  kube-apiserver           │◄────────►│  kubelet                 │
  │    │                      │          │    │                     │
  │  etcd  (the only state)   │          │  container runtime       │
  │                           │          │    (containerd)          │
  │  kube-scheduler           │          │                          │
  │  kube-controller-manager  │◄────────►│  kube-proxy              │
  │  cloud-controller-manager │          │  CNI plugin              │
  └───────────────────────────┘          └──────────────────────────┘
```

Everything talks to the API server. Nothing talks to anything else. The scheduler does not
call the kubelet; the kubelet does not call the scheduler. This hub-and-spoke design is why
components can be restarted, upgraded and replaced independently, and why a single
component failing degrades the cluster in a *specific* way rather than breaking everything.

On this book's cluster, the control plane runs as pods on the control-plane node:

```bash
kubectl get pods -n kube-system
```

```
etcd-k8sbook-control-plane                      Running
kube-apiserver-k8sbook-control-plane            Running
kube-controller-manager-k8sbook-control-plane   Running
kube-scheduler-k8sbook-control-plane            Running
kube-proxy-5z5br / -kvkkt / -vwlf2              Running   (one per node)
kindnet-m7t7c / -rplnh / -54tzh                 Running   (CNI, one per node)
coredns-559f6c778d-htbxc / -tcpbb               Running
```

Those four control-plane pods are **static pods**: manifests in `/etc/kubernetes/manifests`
on the node, started by the kubelet directly rather than by a controller. This solves a
bootstrapping problem — the API server cannot be scheduled by a system that requires the API
server. Chapter 21 uses this: you can fix a broken API server by editing a file on the node,
because the kubelet is watching that directory.

On managed clusters (EKS, GKE, AKS) the control plane is invisible — you will not see these
pods, cannot access etcd, and the provider handles upgrades. Most of this chapter still
matters for understanding behaviour; the operational parts do not.

## kube-apiserver

The front door and the only component that talks to etcd. Everything else — kubectl, the
controllers, the kubelets, your operators — reaches state through it.

It does five things to every request: **authentication** (who are you), **authorization**
(RBAC — may you), **admission** (mutating then validating webhooks, Chapter 24),
**validation** against the schema, and **persistence** to etcd.

That ordering matters for debugging. A request rejected for RBAC never reaches admission; a
request rejected by a webhook never reaches etcd. When something "will not apply", the error
message tells you which stage refused it, and they are very different problems.

It is stateless and horizontally scalable — production clusters run three behind a load
balancer.

**When it fails:** `kubectl` stops working entirely, and no controller can observe or act.
But **running pods keep running**. The kubelet continues managing containers it already
knows about, and containers do not stop. An API server outage is a control-plane outage, not
a data-plane outage. This distinction is worth internalising before your first incident.

## etcd

A distributed key-value store, and the **only** stateful component. Every object in the
cluster is a key in etcd. Lose etcd without a backup and you have lost the cluster's
definition of itself — nodes and containers keep running, but nothing knows what should
exist.

It also provides the **watch** primitive: clients stream changes rather than polling, which
is what makes level-triggered controllers efficient at scale.

Practical facts:

- Run an odd number of members (3 or 5) for quorum. With 3, one may fail; with 5, two.
- It is **latency-sensitive to disk fsync**. Slow disks are the most common cause of a
  mysteriously sluggish cluster — the symptom is high API latency across everything, and the
  cause is not obviously etcd at all.
- Objects have practical size limits (~1 MiB default), which is why very large ConfigMaps
  and Secrets cause trouble.
- **Back it up.** Chapter 21 has the command. A managed control plane does this for you; a
  self-hosted one does not unless you set it up.

## kube-scheduler

Watches for Pods with no `spec.nodeName` and assigns each one a node. That is its whole
job — it does not start containers, and once it has written the node name it never looks at
that pod again.

It runs two phases:

**Filtering** — eliminate nodes that cannot run this pod. Insufficient allocatable CPU or
memory, taints not tolerated, node selectors or affinity unmatched, no available ports,
volume topology constraints, unschedulable nodes.

**Scoring** — rank the survivors. Least-requested resources, image already present on the
node, spreading across zones and hosts, affinity preferences.

Two consequences that cause real confusion:

**Scheduling uses `requests`, not actual usage.** A node with 8 idle CPUs whose pods have
*requested* all 8 will refuse a new pod requesting 1, even though nothing is using anything.
Conversely, a node can be genuinely overloaded while accepting more pods because the running
ones requested very little. Chapter 8 is about this, and Chapter 17 covers the resulting
`Pending`.

**The decision is a point-in-time snapshot.** The scheduler does not rebalance. A pod placed
on a busy node stays there when the cluster empties out, and nothing moves it. Rebalancing
requires a separate tool (`descheduler`) or a pod restart.

**When it fails:** existing pods are unaffected; new pods stay `Pending` forever with no
node assigned. A pod stuck `Pending` with *no events at all* is the signature of an absent
scheduler, as opposed to a scheduler that ran and found nowhere to put it (which produces a
`FailedScheduling` event explaining why).

## kube-controller-manager

A single binary running dozens of controllers in separate goroutines: Deployment,
ReplicaSet, StatefulSet, DaemonSet, Job, CronJob, Node, ServiceAccount, EndpointSlice,
PersistentVolume, namespace, garbage collection, and more.

Each is an independent control loop of the kind Chapter 2 described.

**When it fails:** nothing self-heals. Deleted pods are not replaced, rollouts stall,
EndpointSlices stop updating so Services keep sending traffic to dead pods, and namespaces
will not finish deleting. Existing healthy traffic is unaffected, which makes this failure
quiet and nasty — everything looks fine until something needs to change.

The **cloud-controller-manager** is the provider-specific half: provisioning load balancers
for `type: LoadBalancer` Services, attaching cloud disks, and labelling nodes with zone and
instance type. On kind there is none, which is why a `LoadBalancer` Service stays `<pending>`
forever on a local cluster (Chapter 11).

## kubelet

The agent on every node, and the component that actually makes containers exist. It:

- watches the API server for Pods assigned to *its* node;
- tells the container runtime (containerd) to pull images and start containers, via CRI;
- runs **probes** and reports results (Chapter 9);
- reports node and pod status back;
- manages volume mounts;
- enforces eviction when the node is under resource pressure (Chapter 20);
- runs **static pods** from its manifest directory.

Note who runs probes: **the kubelet, locally**. Probes are not run from the control plane,
which is why a liveness probe still restarts your container during an API server outage, and
why probe traffic never crosses the network between nodes.

**When it fails:** the node goes `NotReady` after a grace period (~40 s of missed
heartbeats). Its containers *keep running* — nothing stops them — but nothing manages them
either. After a further timeout (5 minutes by default) the node controller marks the pods for
deletion and they are rescheduled elsewhere, which means for those five minutes the workload
may be running in two places. Chapter 21 covers this properly; it is the source of the
"split brain" worry people have about node failure, and the mitigation is application-level.

## kube-proxy and the CNI plugin

Two different things, frequently conflated.

**The CNI plugin** (Calico, Cilium, Flannel, or `kindnet` here) gives pods their IP
addresses and makes pod-to-pod traffic route across nodes. Kubernetes itself does not
implement pod networking — it defines the requirements and delegates. The requirement is
strong and worth stating: **every pod gets a unique IP, and every pod can reach every other
pod directly, without NAT.** No port mapping, no NAT between pods, which is a much simpler
model than Docker's default bridge.

Your CNI choice determines whether NetworkPolicy works at all (Chapter 14), and its
performance characteristics.

**kube-proxy** implements *Services*: it watches Services and EndpointSlices and programs
the node so that traffic to a Service's virtual IP is load-balanced to a pod IP. In
`iptables` mode it writes iptables rules; in `ipvs` mode it uses the kernel's load balancer,
which scales better with many Services. Some CNIs (Cilium) replace kube-proxy entirely with
eBPF.

**When kube-proxy fails on a node:** pods on that node cannot reach Service IPs — but *can*
reach pod IPs directly. That asymmetry is a strong diagnostic signal (Chapter 19).

## CoreDNS

Cluster DNS, running as an ordinary Deployment in `kube-system`. It resolves Service names to
ClusterIPs, and pod DNS is configured to point at it.

**When it fails:** name resolution breaks, so almost everything appears broken while
IP-based traffic works fine. Because it is a normal Deployment on normal nodes, it can be
evicted, throttled or OOM-killed like anything else — and a CPU-throttled CoreDNS produces
intermittent, latency-shaped failures across the entire cluster that look like anything but
DNS. Chapter 12 covers this.

## The data plane keeps running

The single most useful operational fact in this chapter, so it gets its own section:

**If the entire control plane disappears, running pods keep serving traffic.**

Containers keep running. kube-proxy's existing rules keep routing. CoreDNS keeps resolving,
if its pods are healthy. What you lose is *change*: no new pods, no rescheduling, no scaling,
no rollouts, no self-healing, and no `kubectl`.

This means a control plane outage is serious but not immediately customer-facing, and it
changes how you triage. It also means "the cluster is down" needs qualifying before you can
act on it.

## Try it

Look at your control plane:

```bash
kubectl get pods -n kube-system -o custom-columns='NAME:.metadata.name,NODE:.spec.nodeName,STATUS:.status.phase'
```

Confirm the control-plane components are static pods — note the `-<nodename>` suffix and
the absence of a ReplicaSet owner:

```bash
kubectl get pod -n kube-system kube-apiserver-k8sbook-control-plane -o jsonpath='{.metadata.ownerReferences[0].kind}{"\n"}'
```

```
Node
```

Owned by the *Node*, not a controller — the signature of a static pod. See them on disk:

```bash
docker exec k8sbook-control-plane ls /etc/kubernetes/manifests/
```

See what the scheduler decided, and that it used requests:

```bash
kubectl get pods -o wide -l app=pingd
```

```bash
kubectl describe node k8sbook-worker | sed -n '/Allocated resources/,/^Events/p'
```

That last block shows requests versus allocatable — the numbers the scheduler actually uses,
which are unrelated to current utilisation.

Watch the API server's request path reject something at the authorization stage:

```bash
kubectl auth can-i delete nodes --as=system:serviceaccount:default:default
```

```
no
```

Prove pod-to-pod networking needs no NAT — connect straight to a pod IP, bypassing Services
entirely:

```bash
POD_IP=$(kubectl get pod -l app=pingd -o jsonpath='{.items[0].status.podIP}') && kubectl run direct --rm -it --restart=Never --image=curlimages/curl:8.11.1 --quiet -- curl -sS "http://$POD_IP:8000/"
```

Finally, see the node heartbeat that determines `Ready`:

```bash
kubectl get node k8sbook-worker -o jsonpath='{range .status.conditions[*]}{.type}={.status} ({.reason}){"\n"}{end}'
```

## Takeaways

- Everything talks to the API server; nothing talks to anything else. Components fail
  independently and in characteristic ways.
- **etcd is the only state.** Back it up. It is fsync-latency-sensitive, and slow disks
  present as cluster-wide API slowness.
- The scheduler assigns nodes using **requests**, once, and never rebalances.
- The controller-manager is where self-healing lives; when it is down, everything looks fine
  until something needs to change.
- The kubelet runs probes **locally** and makes containers exist. A `NotReady` node keeps
  running its containers for ~5 minutes before they are rescheduled elsewhere.
- CNI gives pods IPs and NAT-free pod-to-pod routing; kube-proxy implements Service VIPs.
  "Pod IP works, Service IP does not" points at kube-proxy.
- Static pods are owned by the Node and read from a directory on disk — which is how you fix
  a broken control plane.
- **A control plane outage stops change, not traffic.**

---

Previous: [Chapter 2 — Declarative reconciliation](02-reconciliation.md) ·
Next: [Chapter 4 — kubectl and the resource model](04-kubectl-and-resources.md)

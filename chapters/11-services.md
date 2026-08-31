# Chapter 11 — Services

Pods are ephemeral and their IPs change constantly. A Service is a stable name and virtual
IP in front of a changing set of pods, and it is the only reason anything in a cluster can
reliably talk to anything else.

## The selector is the whole mechanism

```yaml
apiVersion: v1
kind: Service
metadata:
  name: pingd
spec:
  type: ClusterIP
  selector:
    app: pingd
  ports:
    - name: http
      port: 80            # the Service's port
      targetPort: http    # the pod's port (named)
```

**A Service knows nothing about your Deployment.** It matches labels. Whatever has those
labels receives traffic — a Deployment's pods, a StatefulSet's pods, a bare pod you created
by hand, or nothing at all.

That last case is the one to remember: a selector matching nothing is not an error
(Chapter 4). The Service exists, has a ClusterIP, and silently discards traffic.

Use a **named** `targetPort` (`http`) rather than a number. Change the container port later
and the Service follows; hard-code `8000` and you have a silent mismatch.

## EndpointSlices

The Service is the abstraction; **EndpointSlices** are the actual list of backends,
maintained by the endpoint controller.

```bash
kubectl get endpointslice -l kubernetes.io/service-name=pingd -o jsonpath='{.items[0].endpoints[*].addresses[0]}'
```

```
10.244.2.5 10.244.1.4 10.244.2.6
```

These are pod IPs, and they are the first thing to check when a Service does not work
(Chapter 19). Also check the readiness conditions, because a listed endpoint is not
necessarily a routable one:

```bash
kubectl get endpointslice -l kubernetes.io/service-name=pingd -o jsonpath='{.items[0].endpoints[*].conditions.ready}'
```

```
true true true
```

Not-ready pods stay listed with `ready: false` (Chapter 9). Reading only the addresses will
mislead you.

EndpointSlices replaced the older `Endpoints` object, which put every backend in a single
object and became a scalability problem — one pod change rewrote an object listing thousands
of addresses, and every kube-proxy in the cluster received the whole thing. Slices cap at 100
endpoints each. `kubectl get endpoints` still works via a compatibility shim, but
EndpointSlices are what actually drives routing.

## The four types

### ClusterIP (default)

A virtual IP reachable only inside the cluster.

```
clusterIP=10.96.184.46 type=ClusterIP
```

The ClusterIP is **not attached to any network interface**. Nothing listens on it. It exists
only as a set of rules that kube-proxy programs on every node: packets to `10.96.184.46:80`
are rewritten to one of the pod IPs. You cannot ping it meaningfully, and `tcpdump` on the
ClusterIP shows nothing — a genuinely confusing property when debugging.

Load balancing is **per connection**, not per request. A long-lived HTTP/1.1 keepalive
connection or an HTTP/2 connection stays pinned to one pod. This is why gRPC clients famously
do not balance across a ClusterIP Service: one connection, one backend, forever. The answers
are client-side load balancing with a headless Service, or a proxy/service mesh that
understands HTTP/2.

### NodePort

Opens the same port on **every node**, forwarding to the Service.

```bash
kubectl expose deployment pingd --name=pingd-np --type=NodePort --port=80 --target-port=8000
```

```
nodePort=32580
```

Reachable at `<any-node-ip>:32580`, from outside the cluster. The range is 30000–32767 by
default.

NodePort is a building block rather than a destination. Its problems: unmemorable high
ports, you must know node IPs, and no health-aware routing in front of it. It exists mainly
so cloud load balancers have something to target, and for local clusters.

`externalTrafficPolicy` matters here:

- **`Cluster`** (default) — any node accepts the traffic and forwards to a pod anywhere, which
  costs an extra hop and **loses the client source IP** (it is SNATed).
- **`Local`** — only nodes running a pod accept traffic; the source IP is preserved and the
  extra hop is gone. But nodes with no pod blackhole the traffic, so it needs a load balancer
  that health-checks node ports.

"Why does my application see the node's IP instead of the client's?" is this setting.

### LoadBalancer

Asks the cloud provider for an external load balancer pointing at the NodePort.

```bash
kubectl get svc pingd-lb
```

```
NAME       TYPE           CLUSTER-IP     EXTERNAL-IP   PORT(S)
pingd-lb   LoadBalancer   10.96.171.20   <pending>     80:32068/TCP
```

**`<pending>` forever on this cluster**, because kind has no cloud-controller-manager
(Chapter 3). That is not a bug; there is nothing to provision a load balancer. On a local
cluster use `kubectl port-forward` or an Ingress, or install MetalLB if you want real
LoadBalancer semantics.

The other thing to know: **one LoadBalancer Service is one cloud load balancer**, each with a
monthly cost and an IP address. Twenty microservices exposed this way is twenty load
balancers. This is the main practical argument for an Ingress or Gateway (Chapter 13), which
puts many services behind one.

### ExternalName

A CNAME, with no proxying and no endpoints:

```yaml
spec:
  type: ExternalName
  externalName: db.example.com
```

Useful for referring to something outside the cluster by an in-cluster name, so applications
can use `db` in every environment and the mapping changes per cluster.

## Headless Services

`clusterIP: None`. No virtual IP, no proxying — DNS returns **the pod IPs directly**.

Measured against the two Services in this book's example:

```bash
nslookup pingd            # normal ClusterIP Service
nslookup pingd-headless   # headless
```

```
pingd-headless.default.svc.cluster.local
Address: 10.244.1.8
Address: 10.244.2.12
Address: 10.244.2.11
```

Three A records, one per pod, instead of a single VIP.

Use a headless Service when:

- the client should do its own load balancing (gRPC, database drivers with connection pools);
- you need per-pod DNS names — a StatefulSet requires a headless Service for
  `web-0.web-headless.default.svc.cluster.local` (Chapter 10);
- something needs to discover all peers, like a clustered database forming a quorum.

The caveat: DNS clients cache, and many take the first record. Round-robin DNS is not load
balancing (the same warning as the Docker book's Chapter 11).

## Multi-port Services and ports for the SIP case

```yaml
ports:
  - name: http
    port: 80
    targetPort: http
  - name: metrics
    port: 9090
    targetPort: metrics
```

Names are **required** once there is more than one port.

UDP is supported (`protocol: UDP`), and SCTP exists but with patchy support. Two things are
worth knowing for anything media-related:

**A Service cannot express a large dynamic port range.** RTP typically needs thousands of UDP
ports, and Services enumerate ports individually. The practical answers are `hostNetwork:
true` (Chapter 5's scheduling constraints then apply, and you lose port isolation), or
`hostPort` on specific ports, or a specialised ingress that understands the protocol.

**kube-proxy's UDP conntrack handling has historically been a source of stale entries**,
where a UDP "connection" keeps being sent to a pod that no longer exists. If you see UDP
traffic going nowhere after a pod restart, that is the first thing to check.

For SIP specifically, the NAT problem from the Docker book returns in a new form: pod IPs are
routable within the cluster but not outside it, so SDP advertising a pod IP fails for external
peers exactly as it did with Docker's bridge. `hostNetwork` plus the application's
public-address override remains the working answer.

## Session affinity

```yaml
spec:
  sessionAffinity: ClientIP
  sessionAffinityConfig:
    clientIP:
      timeoutSeconds: 10800
```

Pins a client IP to one backend. Crude — it is source-IP based, so everyone behind one NAT
is one client — and it defeats even load distribution. If you need sticky sessions, do it at
the HTTP layer with an Ingress (cookie-based), and better still make your application
stateless.

## Traffic distribution

```yaml
spec:
  trafficDistribution: PreferClose
```

A newer, simpler alternative to topology-aware hints: prefer endpoints in the same zone,
falling back cluster-wide. This reduces cross-zone traffic charges, which are a real cost on
cloud providers. Note it is a *preference*, not a guarantee.

## Try it

Look at the Service and its backends:

```bash
kubectl get svc pingd -o wide
```

```bash
kubectl get endpointslice -l kubernetes.io/service-name=pingd -o custom-columns='NAME:.metadata.name,ENDPOINTS:.endpoints[*].addresses[0],READY:.endpoints[*].conditions.ready'
```

Reach it by name from another pod:

```bash
kubectl run t --rm -it --restart=Never --image=curlimages/curl:8.11.1 --quiet -- curl -sS http://pingd/
```

Compare a ClusterIP with a headless Service — a single VIP versus three pod IPs:

```bash
kubectl run t --rm -it --restart=Never --image=busybox:1.37 --quiet -- sh -c 'nslookup pingd | tail -3; echo ---; nslookup pingd-headless | tail -6'
```

Create a NodePort and reach it via a node IP:

```bash
kubectl expose deployment pingd --name=pingd-np --type=NodePort --port=80 --target-port=8000
```

```bash
NP=$(kubectl get svc pingd-np -o jsonpath='{.spec.ports[0].nodePort}') && NODE=$(kubectl get node k8sbook-worker -o jsonpath='{.status.addresses[0].address}') && kubectl run t --rm -it --restart=Never --image=curlimages/curl:8.11.1 --quiet -- curl -sS "http://$NODE:$NP/"
```

Watch a LoadBalancer stay pending with no cloud controller:

```bash
kubectl expose deployment pingd --name=pingd-lb --type=LoadBalancer --port=80 --target-port=8000 && sleep 5 && kubectl get svc pingd-lb
```

```
pingd-lb   LoadBalancer   10.96.171.20   <pending>   80:32068/TCP
```

Now create the classic silent failure — a Service whose selector matches nothing:

```bash
kubectl create service clusterip nobody --tcp=80:8000
```

```bash
kubectl get svc nobody && kubectl get endpointslice -l kubernetes.io/service-name=nobody
```

Healthy-looking Service, `<unset>` endpoints. Clean up:

```bash
kubectl delete svc pingd-np pingd-lb nobody
```

## Takeaways

- A Service matches **labels**, not Deployments. A selector matching nothing is silent.
- Use named `targetPort`s so a container port change cannot break routing.
- **EndpointSlices are the real backend list.** Check both addresses and `conditions.ready`.
- A ClusterIP is not on any interface — it is only kube-proxy rules, which is why you cannot
  tcpdump it.
- Load balancing is **per connection**. HTTP/2 and gRPC pin to one pod; use a headless Service
  with client-side balancing or a proxy.
- NodePort loses the client source IP unless `externalTrafficPolicy: Local`.
- One LoadBalancer Service is one cloud load balancer, with a bill. Use Ingress for many
  services.
- `<pending>` EXTERNAL-IP means no cloud-controller-manager — expected locally.
- Headless Services return pod IPs and are required for StatefulSet per-pod DNS.
- Services cannot express large dynamic UDP ranges; media workloads need `hostNetwork`.

---

Previous: [Chapter 10 — The other workload kinds](10-other-workloads.md) ·
Next: [Chapter 12 — DNS and service discovery](12-dns.md)

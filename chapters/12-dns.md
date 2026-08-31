# Chapter 12 — DNS and service discovery

Every pod is configured to use cluster DNS, and almost every inter-service call in a cluster
starts with a DNS lookup. It is also a component that fails in ways that look like anything
but DNS, and whose default configuration wastes a surprising amount of traffic.

## What a pod gets

```bash
kubectl run t --rm -it --restart=Never --image=busybox:1.37 -- cat /etc/resolv.conf
```

```
search default.svc.cluster.local svc.cluster.local cluster.local
nameserver 10.96.0.10
options ndots:5
```

Three parts, all of which matter:

- **`nameserver 10.96.0.10`** — the ClusterIP of the `kube-dns` Service, which fronts CoreDNS.
  (The Service is still called `kube-dns` for compatibility, though CoreDNS replaced kube-dns
  years ago.)
- **`search ...`** — domains appended to unqualified names, in order. Note the first entry is
  the **pod's own namespace**, which is why a bare `pingd` resolves within a namespace and not
  across namespaces.
- **`options ndots:5`** — the setting that costs you.

## Names

```
<service>                                    # same namespace
<service>.<namespace>                        # cross-namespace
<service>.<namespace>.svc                    # more explicit
<service>.<namespace>.svc.cluster.local      # fully qualified
<service>.<namespace>.svc.cluster.local.     # absolute — no search walk
```

For headless Services, each pod gets its own record, which is what StatefulSets depend on:

```
<pod>.<service>.<namespace>.svc.cluster.local
web-0.web-headless.default.svc.cluster.local
```

## `ndots:5`, and why external lookups are slow

This is the practical problem, and it is worth understanding precisely.

`ndots:5` means: **if a name contains fewer than 5 dots, try the search domains first.**

`github.com` has one dot. So a lookup for `github.com` from a pod does this:

```
github.com.default.svc.cluster.local   -> NXDOMAIN
github.com.svc.cluster.local           -> NXDOMAIN
github.com.cluster.local               -> NXDOMAIN
github.com                             -> answer
```

Measured on this cluster — the first two, confirmed NXDOMAIN:

```
github.com.default.svc.cluster.local    -> NXDOMAIN
github.com.svc.cluster.local            -> NXDOMAIN
```

**Four queries to resolve one name, three of them guaranteed to fail.** And because most
resolvers query both A and AAAA, it is typically eight packets. Multiply by every external
call your services make.

The consequences: added latency on every external request, three times the necessary load on
CoreDNS, and — at scale — CoreDNS becoming a bottleneck for reasons that look like a DNS
problem but are really a configuration default.

In-cluster names are unaffected: `pingd` matches on the *first* search domain, so a
short in-namespace name is optimal. The waste is entirely on external names.

Three fixes:

**1. Use absolute names for external hosts.** A trailing dot ends the search:

```yaml
env:
  - name: API_ENDPOINT
    value: "https://api.example.com./v1"
```

Effective, and it looks like a typo to everyone who reads it. Comment it.

**2. Lower `ndots` per pod:**

```yaml
spec:
  dnsConfig:
    options:
      - name: ndots
        value: "2"
```

With `ndots:2`, `github.com` (1 dot) is still searched, but `api.example.com` (2 dots) is
tried absolute first. Going below 2 breaks short in-cluster names, so 2 is usually the floor.

**3. Run NodeLocal DNSCache** — a DaemonSet caching DNS on each node, which removes most of
the cross-node traffic and the conntrack pressure. This is the standard answer for large
clusters and worth deploying before you need it.

## CoreDNS

An ordinary Deployment in `kube-system`, configured by a ConfigMap:

```bash
kubectl get cm -n kube-system coredns -o jsonpath='{.data.Corefile}'
```

```
.:53 {
    errors
    health { lameduck 5s }
    ready
    kubernetes cluster.local in-addr.arpa ip6.arpa {
       pods insecure
       fallthrough in-addr.arpa ip6.arpa
       ttl 30
    }
    prometheus :9153
    forward . /etc/resolv.conf { max_concurrent 1000 }
    cache 30 { disable success cluster.local
               disable denial cluster.local }
    loop
    reload
    loadbalance
}
```

Reading it: the `kubernetes` plugin answers for `cluster.local` with a **30-second TTL**;
`forward` sends everything else to the node's own resolvers; `cache 30` caches for 30 seconds
but explicitly **disables caching for `cluster.local`**, since the kubernetes plugin is
authoritative and already fast.

That 30-second TTL is worth knowing: a client that caches DNS aggressively can keep using a
Service's old ClusterIP or a headless Service's stale pod IPs for far longer than intended.
JVMs with `networkaddress.cache.ttl=-1` are the classic offender.

Useful additions, edited into that ConfigMap:

```
# Send an internal zone to a specific upstream
corp.internal:53 {
    forward . 10.0.0.53
}
```

```
# Rewrite one name to another
rewrite name legacy-api.default.svc.cluster.local pingd.default.svc.cluster.local
```

CoreDNS reloads automatically (the `reload` plugin) within a minute or two of a ConfigMap
change.

### CoreDNS is a failure domain

Because it is a normal Deployment on normal nodes, it can be evicted, CPU-throttled or
OOM-killed like anything else. When it degrades, **every service in the cluster experiences
intermittent, latency-shaped failures**, and almost nobody's first hypothesis is DNS.

Practical protections:

- Give it resource **requests** so it is not BestEffort (Chapter 8) — this is the single most
  common CoreDNS mistake.
- Do not set aggressive CPU limits on it; throttled DNS is a cluster-wide latency source.
- Spread replicas across nodes with anti-affinity or topology spread.
- Scale replicas with cluster size — the rough guidance is 2 minimum, plus one per ~16 nodes,
  and watch the metrics rather than trusting the formula.
- Add a PodDisruptionBudget (Chapter 25) so a node drain cannot take all replicas at once.
- Alert on `coredns_dns_responses_total{rcode="SERVFAIL"}` and request duration.

## Per-pod DNS configuration

```yaml
spec:
  dnsPolicy: ClusterFirst        # default
  dnsConfig:
    nameservers: ["10.0.0.53"]
    searches: ["corp.internal"]
    options:
      - name: ndots
        value: "2"
```

`dnsPolicy` values:

- **`ClusterFirst`** (default) — cluster DNS, forwarding external names upstream.
- **`ClusterFirstWithHostNet`** — what you need with `hostNetwork: true`. **This is a real
  trap**: a `hostNetwork` pod with the default policy uses the *node's* resolver and cannot
  resolve any Service name at all. If a host-networked pod cannot find `pingd`, this is why —
  and it applies directly to the SIP workload from Chapter 11, which needs host networking.
- **`Default`** — inherit the node's resolvers, ignoring cluster DNS.
- **`None`** — use only what you specify in `dnsConfig`.

## Debugging DNS

The sequence, in order:

```bash
kubectl exec -it POD -- cat /etc/resolv.conf
```

Wrong nameserver or missing search domains means a pod-level misconfiguration or a
`hostNetwork` policy problem.

```bash
kubectl run t --rm -it --restart=Never --image=nicolaka/netshoot -- dig pingd.default.svc.cluster.local
```

Resolving from a fresh pod but not from yours narrows it to your pod's configuration or its
resolver library.

```bash
kubectl get svc -n kube-system kube-dns
kubectl get pods -n kube-system -l k8s-app=kube-dns
kubectl logs -n kube-system -l k8s-app=kube-dns --tail=50
```

```bash
kubectl get endpointslice -n kube-system -l kubernetes.io/service-name=kube-dns
```

No endpoints for `kube-dns` means CoreDNS pods are not ready, and *nothing in the cluster can
resolve anything*.

Two special cases worth recognising:

**Intermittent failures, usually ~5 seconds.** Classic musl/glibc parallel A+AAAA race
against conntrack, producing occasional lost responses and a 5-second resolver timeout. Alpine
images are more affected. Mitigations: `single-request-reopen` in `dnsConfig`, NodeLocal
DNSCache, or a CNI that avoids the conntrack path.

**`Name does not resolve` for a Service that exists.** Check the namespace — a bare name only
resolves within the pod's own namespace, because that is the first search domain.

## Try it

Look at what your pods actually get:

```bash
kubectl run t --rm -it --restart=Never --image=busybox:1.37 --quiet -- cat /etc/resolv.conf
```

Watch the search walk waste three queries on an external name:

```bash
kubectl run t --rm -it --restart=Never --image=nicolaka/netshoot --quiet -- sh -c 'for s in default.svc.cluster.local svc.cluster.local cluster.local; do printf "github.com.%s -> " "$s"; dig +short +time=1 "github.com.$s" | head -1 | grep -q . && echo ANSWER || echo NXDOMAIN; done'
```

```
github.com.default.svc.cluster.local -> NXDOMAIN
github.com.svc.cluster.local         -> NXDOMAIN
github.com.cluster.local             -> NXDOMAIN
```

Then confirm the trailing dot skips it:

```bash
kubectl run t --rm -it --restart=Never --image=nicolaka/netshoot --quiet -- sh -c 'time dig +short github.com. | head -1'
```

Resolve a Service the four different ways:

```bash
kubectl run t --rm -it --restart=Never --image=nicolaka/netshoot --quiet -- sh -c 'for n in pingd pingd.default pingd.default.svc pingd.default.svc.cluster.local; do printf "%-40s -> " "$n"; dig +short "$n"; done'
```

See per-pod DNS records from a headless Service:

```bash
kubectl run t --rm -it --restart=Never --image=nicolaka/netshoot --quiet -- dig +short pingd-headless.default.svc.cluster.local
```

Check CoreDNS health, and specifically whether it has resource requests:

```bash
kubectl get deploy -n kube-system coredns -o jsonpath='{.spec.template.spec.containers[0].resources}{"\n"}'
```

```bash
kubectl get endpointslice -n kube-system -l kubernetes.io/service-name=kube-dns -o jsonpath='{.items[0].endpoints[*].addresses[0]}{"\n"}'
```

## Takeaways

- Pods get `nameserver <kube-dns ClusterIP>`, a search list starting with **their own
  namespace**, and `ndots:5`.
- **`ndots:5` makes every external lookup cost four queries, three of them NXDOMAIN.** Fix
  with trailing-dot absolute names, `ndots:2` via `dnsConfig`, or NodeLocal DNSCache.
- A bare Service name only resolves inside the pod's namespace. Cross-namespace needs
  `svc.namespace`.
- CoreDNS is a normal Deployment and therefore a cluster-wide failure domain. Give it
  requests, avoid tight CPU limits, spread it, add a PDB.
- Records have a 30-second TTL; aggressively caching clients will hold stale addresses.
- **`hostNetwork: true` needs `dnsPolicy: ClusterFirstWithHostNet`**, or Service names do not
  resolve at all.
- Intermittent ~5-second DNS failures are the A/AAAA conntrack race, not your application.

---

Previous: [Chapter 11 — Services](11-services.md) ·
Next: [Chapter 13 — Ingress and Gateway API](13-ingress.md)

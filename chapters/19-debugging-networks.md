# Chapter 19 — Network diagnosis

"Service X cannot reach Service Y" has about eight plausible causes in Kubernetes. This
chapter walks the packet from client to container and eliminates them in order.

## Follow the path

```
client pod
  ├─ 1. DNS: does the Service name resolve?
  ├─ 2. Service: does it have ready endpoints?
  ├─ 3. Policy: is the traffic allowed?
  ├─ 4. kube-proxy: is the ClusterIP programmed on this node?
  ├─ 5. CNI: can pod reach pod?
  └─ 6. Application: is it listening on the right address and port?
```

Test in that order. Each step is one command and eliminates a whole class of cause.

## Step 1: does the name resolve?

```bash
kubectl run t --rm -it --restart=Never --image=nicolaka/netshoot -- nslookup pingd
```

Failure means DNS (Chapter 12). Check the namespace first — a bare name only resolves within
the pod's own namespace, because that is the first search domain. Cross-namespace needs
`pingd.othernamespace`.

If a *fresh* pod resolves it and yours does not, compare `/etc/resolv.conf`; a `hostNetwork`
pod with the wrong `dnsPolicy` is the usual answer.

## Step 2: does the Service have ready endpoints?

**This is the most common cause and the cheapest check. Do it first in practice.**

```bash
kubectl get endpointslice -l kubernetes.io/service-name=pingd \
  -o custom-columns='NAME:.metadata.name,ADDRESSES:.endpoints[*].addresses[0],READY:.endpoints[*].conditions.ready'
```

```
NAME          ADDRESSES                            READY
pingd-abc12   10.244.2.5,10.244.1.4,10.244.2.6     true,true,true
```

Three outcomes:

**`<unset>` / no addresses** — the selector matches no pods. Compare them:

```bash
kubectl get svc pingd -o jsonpath='{.spec.selector}{"\n"}'
```

```bash
kubectl get pods -l app=pingd --show-labels
```

A mismatch here is the single most common Kubernetes networking bug, and nothing warns you
(Chapter 4). It usually arrives via a typo, a copied manifest, or a Deployment whose pod
template labels drifted from the Service's selector.

**Addresses present but `ready: false`** — the pods exist and fail readiness. This is a
Chapter 9 problem, not a networking one. Remember that not-ready endpoints stay *listed*, so
reading only the addresses column will mislead you.

**Addresses present and ready** — the Service is fine; continue to step 3.

Also verify the ports line up:

```bash
kubectl get svc pingd -o jsonpath='port={.spec.ports[0].port} targetPort={.spec.ports[0].targetPort}{"\n"}'
```

A `targetPort` that does not match the container's actual listening port gives you a healthy
Service with ready endpoints that refuses every connection.

## Step 3: is a NetworkPolicy blocking it?

```bash
kubectl get networkpolicy -A
```

If any policy selects the destination pod, everything not explicitly allowed is denied
(Chapter 14). The specific traps to check:

- The policy uses the **Service port** rather than the **pod port**.
- An egress policy on the *client* forgot to allow DNS, so step 1 fails.
- `namespaceSelector` and `podSelector` in separate list items (OR) rather than the same item
  (AND) — or the reverse.

```bash
kubectl describe networkpolicy -n NAMESPACE POLICY
```

The fastest test is to temporarily label a debug pod so a policy permits it, and see whether
that changes the outcome.

## Step 4: bypass the Service

Narrow it to Service routing versus everything else. Connect straight to a pod IP:

```bash
POD_IP=$(kubectl get pod -l app=pingd -o jsonpath='{.items[0].status.podIP}')
```

```bash
kubectl run t --rm -it --restart=Never --image=curlimages/curl:8.11.1 -- curl -sS "http://$POD_IP:8000/"
```

- **Pod IP works, ClusterIP does not** → kube-proxy or the Service definition. Check kube-proxy
  is running on the *client's* node, and check `targetPort`.
- **Neither works** → the CNI, a policy, or the application (steps 3, 5, 6).

Then bypass the network entirely:

```bash
kubectl port-forward deploy/pingd 8080:8000
```

```bash
curl -sS localhost:8080/
```

If that works, the application is listening correctly and every remaining suspect is in the
network path.

## Step 5: kube-proxy and the CNI

```bash
kubectl get pods -n kube-system -l k8s-app=kube-proxy -o wide
```

```bash
kubectl logs -n kube-system -l k8s-app=kube-proxy --tail=30
```

kube-proxy failing on **one** node produces a distinctive symptom: pods on that node cannot
reach any ClusterIP, but *can* reach pod IPs directly. Everywhere else works. If your failures
correlate with the client's node, this is where to look.

Inspect the rules on a node (Chapter 18's node debugger):

```bash
kubectl debug node/k8sbook-worker -it --image=nicolaka/netshoot -- \
  chroot /host sh -c 'iptables-save -t nat | grep pingd | head'
```

For the CNI, check its DaemonSet is healthy on every node and look for IP exhaustion:

```bash
kubectl get pods -n kube-system -o wide | grep -E 'calico|cilium|kindnet|flannel'
```

`failed to setup network for sandbox` in a pod's events means the CNI could not allocate an
address — often the node's pod CIDR is full.

## Step 6: the application

Two causes that the Docker book already covered, both of which recur here unchanged.

**Bound to `127.0.0.1` inside the container.** Reachable only within the pod's own network
namespace — not from another pod, no matter how correct the Service is. Check from an
ephemeral container sharing the pod's network:

```bash
kubectl debug POD --image=nicolaka/netshoot -it -- ss -tlnp
```

```
127.0.0.1:8000        ← wrong, unreachable
0.0.0.0:8000          ← right
```

**`localhost` resolves to `::1`.** An IPv4-only server refuses connections to
`localhost:8000` while accepting `127.0.0.1:8000`. This bites hardest in **probes**, which
target the local pod — a healthcheck using `localhost` fails against a working server. Use
`127.0.0.1`.

## Ingress-specific

Chapter 13's failure is worth repeating as a diagnostic, because it looked like a routing bug
and was not:

```
curl: (56) Recv failure: Connection reset by peer
```

The Ingress was correct; the **controller pod was on a node where external traffic never
arrives**. Check where it is and how traffic reaches it:

```bash
kubectl get pod -n ingress-nginx -l app.kubernetes.io/component=controller \
  -o custom-columns='NAME:.metadata.name,NODE:.spec.nodeName,HOSTPORT:.spec.containers[0].ports[*].hostPort'
```

Then work outward:

```bash
kubectl get ingressclass                                   # is there a controller?
kubectl describe ingress NAME                              # did it resolve the backend?
kubectl logs -n ingress-nginx deploy/ingress-nginx-controller --tail=50
```

A 404 from the ingress controller usually means no rule matched — check the `Host` header and
`pathType` semantics. A 503 means the rule matched but the backend has no endpoints, which
sends you back to step 2.

## Capturing traffic

When the logic checks out and it still fails, look at the wire. An ephemeral container in the
pod's network namespace:

```bash
kubectl debug POD --image=nicolaka/netshoot -it -- tcpdump -i any -n port 8000
```

Interpretation is the same as the Docker book:

- **Nothing** — the client never sent; it resolved somewhere else.
- **SYN, no reply** — dropped. Policy, wrong address, or nothing listening on that interface.
- **SYN → RST** — refused. Nothing listening on that port.
- **Handshake then reset** — past the network; TLS or the application rejected it.

## A quick reference

| Symptom | Most likely |
|---|---|
| Name does not resolve | Wrong namespace; CoreDNS; `hostNetwork` dnsPolicy |
| Resolves, connection refused | `targetPort` wrong; app on `127.0.0.1`; `::1` vs `127.0.0.1` |
| Resolves, connection times out | NetworkPolicy; CNI; wrong node |
| Service has no endpoints | **Selector does not match pod labels** |
| Endpoints exist but `ready: false` | Readiness probe failing (Chapter 9) |
| Works from some pods only | kube-proxy on the client's node; policy by namespace |
| Ingress 503 | Backend Service has no ready endpoints |
| Ingress 404 | No rule matched — Host header or `pathType` |
| Ingress connection reset | Controller not where traffic arrives |

## Try it

Create the classic failure — a Service whose selector matches nothing:

```bash
kubectl create service clusterip broken --tcp=80:8000
```

```bash
kubectl get svc broken && kubectl get endpointslice -l kubernetes.io/service-name=broken
```

`ENDPOINTS: <unset>`. Now compare selector to labels, which is the diagnosis:

```bash
kubectl get svc broken -o jsonpath='selector={.spec.selector}{"\n"}'; kubectl get pods -l app=pingd --show-labels --no-headers | head -1
```

```bash
kubectl delete svc broken
```

Now walk the working path deliberately. DNS:

```bash
kubectl run t --rm -it --restart=Never --image=nicolaka/netshoot --quiet -- nslookup pingd
```

Endpoints:

```bash
kubectl get endpointslice -l kubernetes.io/service-name=pingd -o custom-columns='ADDRESSES:.endpoints[*].addresses[0],READY:.endpoints[*].conditions.ready'
```

Pod IP directly, bypassing the Service:

```bash
POD_IP=$(kubectl get pod -l app=pingd -o jsonpath='{.items[0].status.podIP}') && kubectl run t --rm -it --restart=Never --image=curlimages/curl:8.11.1 --quiet -- curl -sS "http://$POD_IP:8000/"
```

Via the Service:

```bash
kubectl run t --rm -it --restart=Never --image=curlimages/curl:8.11.1 --quiet -- curl -sS http://pingd/
```

And bypassing the network entirely:

```bash
kubectl port-forward deploy/pingd 8080:8000 & sleep 3; curl -sS localhost:8080/; kill %1
```

Finally, check what the application is actually bound to:

```bash
POD=$(kubectl get pod -l app=pingd -o jsonpath='{.items[0].metadata.name}') && kubectl debug $POD --image=nicolaka/netshoot -it --quiet -- ss -tln
```

`0.0.0.0:8000` is what you want to see.

## Takeaways

- Walk the path: DNS → endpoints → policy → kube-proxy → CNI → application.
- **Check endpoints first in practice.** A selector that matches no pods is the most common
  cause and produces no error anywhere.
- Read `conditions.ready`, not just addresses — not-ready pods stay listed.
- A `targetPort` mismatch gives you a healthy-looking Service that refuses every connection.
- Pod IP works but ClusterIP does not → kube-proxy or the Service. Neither works → CNI, policy
  or the app.
- `port-forward` bypasses Service, DNS and Ingress — the fastest way to exonerate the
  application.
- kube-proxy broken on one node: that node's pods cannot reach any ClusterIP but can reach pod
  IPs.
- The application bound to `127.0.0.1`, and `localhost` meaning `::1`, are still the two most
  common application-side causes — especially in probes.
- Ingress 503 = no ready endpoints; 404 = no rule matched; connection reset = controller not
  where traffic arrives.

---

Previous: [Chapter 18 — Getting inside](18-getting-inside.md) ·
Next: [Chapter 20 — Resources and eviction](20-resources-and-eviction.md)

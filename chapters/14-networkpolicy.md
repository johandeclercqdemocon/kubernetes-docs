# Chapter 14 — NetworkPolicy

**By default, every pod in a cluster can reach every other pod, in every namespace.** No
firewall, no segmentation, no restrictions. A compromised frontend can talk directly to your
database, your internal admin service and the cloud metadata endpoint.

NetworkPolicy is how you change that. It is also the feature most often assumed to be working
when it is not.

## The prerequisite nobody checks

**NetworkPolicy objects do nothing unless your CNI plugin implements them.** The API accepts
them, `kubectl get networkpolicy` lists them, and if your CNI ignores policy they have
precisely zero effect. There is no warning.

Flannel, notably, does not enforce NetworkPolicy. Calico, Cilium, Antrea, Weave and kindnet
do.

So before writing any policy, **test that enforcement works**. The procedure is in Try it
below, and it takes a minute. Deploying policies onto a cluster that ignores them is worse
than having none, because you believe you are protected.

## The model

Three rules that explain all the confusing behaviour:

**1. Policies are additive allow-lists.** There is no `deny` rule. A policy grants
permission; multiple policies selecting a pod are unioned.

**2. A pod is "selected" if any policy's `podSelector` matches it.** Once selected for a
direction (Ingress or Egress), **everything not explicitly allowed in that direction is
denied**. An unselected pod is completely unrestricted.

**3. Ingress and Egress are independent.** A policy with `policyTypes: [Ingress]` does not
restrict outbound traffic at all.

The consequence people trip over: a policy that selects a pod but lists **no rules** is a
*deny-all* for that direction. That is not a special syntax — it is rule 2 with an empty
allow-list.

## Default deny

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: default-deny-ingress
spec:
  podSelector: {}            # every pod in this namespace
  policyTypes: [Ingress]     # no ingress rules → deny all inbound
```

`podSelector: {}` means *all pods in the namespace*. Note that NetworkPolicy is namespaced —
this affects one namespace, and you need it in every namespace you want protected.

Then allow what is needed:

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-pingd-from-client
spec:
  podSelector:
    matchLabels: { app: pingd }
  policyTypes: [Ingress]
  ingress:
    - from:
        - podSelector:
            matchLabels: { role: client }
      ports:
        - protocol: TCP
          port: 8000
```

Measured on this cluster, with both policies applied:

```
no label      -> blocked
role=client   -> 200
role=other    -> blocked
```

Traffic to `pingd` succeeds only from pods labelled `role=client`, on port 8000. Both the
Service ClusterIP path and a direct pod-IP connection behave identically, which is expected:
kube-proxy DNATs to the pod IP before policy is evaluated, so policy sees the same source and
destination either way.

**Note the port is the pod's port (8000), not the Service's port (80).** Policy operates on
pod-level traffic after DNAT. Writing `port: 80` here silently blocks everything, and it is
one of the most common NetworkPolicy mistakes.

## Selecting sources

Four kinds of `from`/`to` peer, and the distinction between two of them causes real
misconfiguration:

```yaml
ingress:
  - from:
      # (a) pods in the SAME namespace
      - podSelector:
          matchLabels: { role: client }

      # (b) ALL pods in namespaces matching this label
      - namespaceSelector:
          matchLabels: { kubernetes.io/metadata.name: monitoring }

      # (c) specific pods in specific namespaces  ← note: ONE list item
      - namespaceSelector:
          matchLabels: { team: platform }
        podSelector:
          matchLabels: { app: prometheus }

      # (d) IP ranges (for traffic from outside the cluster)
      - ipBlock:
          cidr: 10.0.0.0/8
          except: ["10.5.0.0/16"]
```

**YAML list structure is the trap.** In (c), `namespaceSelector` and `podSelector` are in the
*same* list item, meaning "pods matching X **in** namespaces matching Y" — an AND. Put them
in separate list items (each with its own `-`) and you get an OR: "all pods in those
namespaces, **or** those pods in any namespace". That is dramatically more permissive, and
the YAML difference is a single character.

The label `kubernetes.io/metadata.name` is automatically set on every namespace, so you can
select a namespace by name without adding labels yourself.

## Egress

More valuable than ingress for containing a compromise, and much more disruptive to get
wrong.

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: pingd-egress
spec:
  podSelector:
    matchLabels: { app: pingd }
  policyTypes: [Egress]
  egress:
    # DNS — required, and the thing everyone forgets
    - to:
        - namespaceSelector:
            matchLabels: { kubernetes.io/metadata.name: kube-system }
          podSelector:
            matchLabels: { k8s-app: kube-dns }
      ports:
        - protocol: UDP
          port: 53
        - protocol: TCP
          port: 53
    # the database
    - to:
        - podSelector:
            matchLabels: { app: postgres }
      ports:
        - protocol: TCP
          port: 5432
```

**Always allow DNS explicitly.** An egress policy without a DNS rule breaks name resolution
for the selected pods, and the symptom — everything failing with resolution errors — rarely
points people at the network policy they just applied. Both UDP and TCP port 53; large
responses fall back to TCP.

Egress policy is also how you block access to the **cloud metadata endpoint**
(`169.254.169.254`), which is a genuine privilege-escalation path on cloud providers:

```yaml
  egress:
    - to:
        - ipBlock:
            cidr: 0.0.0.0/0
            except:
              - 169.254.169.254/32
```

## What NetworkPolicy cannot do

Being clear about the limits, because it is often oversold:

- **No L7 awareness.** It is IPs, ports and protocols. You cannot allow `GET /api/public`
  while denying `POST /admin`. That needs a service mesh or an application-layer proxy.
- **No egress-by-DNS-name.** `ipBlock` takes CIDRs. Allowing "api.stripe.com" is not
  expressible, because the addresses behind a name change. Several CNIs offer this as a
  proprietary extension (Cilium's `CiliumNetworkPolicy` has `toFQDNs`), but it is not
  standard.
- **No logging in the standard API.** You cannot see what was denied. Most CNIs provide this
  separately (Cilium's Hubble, Calico's flow logs), and you will want it — silent drops are
  miserable to debug.
- **Node-local traffic is inconsistent.** Traffic from a node's own kubelet — health probes,
  for instance — may bypass policy depending on the CNI. Do not rely on policy to block it.
- **Not a strong tenant boundary.** It restricts network reachability. Pods still share a
  kernel (Chapter 28).

## A workable adoption path

Applying default-deny to a running cluster breaks things immediately and in ways that are
hard to trace. A safer sequence:

1. **Verify your CNI enforces policy** at all.
2. **Start in one non-critical namespace.**
3. **Write egress policies first** — they are more valuable and their failures are more
   obvious.
4. **Always include DNS.**
5. **Apply default-deny last**, after the allow rules exist.
6. **Get flow logs** from your CNI before you need them.
7. **Test from both sides** — that allowed traffic works, and that denied traffic is denied.
   Only testing the first half is how you end up with policies that permit everything.

For generated starting points, `kubectl get networkpolicy -o yaml` on a mature cluster and
tools like Cilium's policy editor help; do not deploy generated policy unread.

## Try it

**First, verify enforcement.** Baseline — any pod can reach `pingd`:

```bash
kubectl run t --rm -it --restart=Never --image=curlimages/curl:8.11.1 --quiet -- curl -sS -m5 http://pingd/
```

```json
{"service":"pingd","version":"1.0.0","reply":"pong"}
```

Apply default-deny plus a narrow allow:

```bash
kubectl apply -f examples/manifests/14-networkpolicy.yaml && sleep 6
```

Unlabelled pod — should now fail:

```bash
kubectl run t --rm -it --restart=Never --image=curlimages/curl:8.11.1 --quiet -- curl -sS -m6 http://pingd/
```

If that **succeeds**, your CNI is not enforcing policy and everything else in this chapter is
decorative on your cluster. Find out before relying on it.

Labelled pod — should succeed:

```bash
kubectl run t --rm -it --restart=Never --labels='role=client' --image=curlimages/curl:8.11.1 --quiet -- curl -sS -m6 -o /dev/null -w '%{http_code}\n' http://pingd/
```

```
200
```

Wrong label — should fail, proving it is the label and not merely "having a label":

```bash
kubectl run t --rm -it --restart=Never --labels='role=other' --image=curlimages/curl:8.11.1 --quiet -- curl -sS -m6 http://pingd/
```

Confirm egress is untouched, since we only set `policyTypes: [Ingress]`:

```bash
kubectl run t --rm -it --restart=Never --labels='role=client' --image=curlimages/curl:8.11.1 --quiet -- curl -sS -m6 -o /dev/null -w 'external=%{http_code}\n' https://example.com/
```

Inspect what a policy resolved to:

```bash
kubectl describe networkpolicy allow-pingd-from-client
```

Clean up — **⚠️ leaving default-deny in place will break the rest of the book's examples**:

```bash
kubectl delete -f examples/manifests/14-networkpolicy.yaml
```

## Takeaways

- Default is **allow everything, everywhere, across namespaces**.
- **NetworkPolicy does nothing unless your CNI implements it.** Flannel does not. Test
  enforcement before trusting it.
- Policies are additive allow-lists. Selecting a pod with no rules for a direction denies
  that direction entirely.
- **Use the pod's port, not the Service's port.** Policy is evaluated after DNAT.
- `namespaceSelector` and `podSelector` in the *same list item* is AND; in separate items it
  is OR, and far more permissive. One character of YAML.
- **Always allow DNS in egress policies** (UDP and TCP 53), or name resolution silently
  breaks.
- Egress policy can block the cloud metadata endpoint — a real escalation path.
- No L7 rules, no DNS-name egress, no logging in the standard API. Get flow logs from your
  CNI.
- Adopt incrementally: allow rules first, default-deny last, test both directions.

---

Previous: [Chapter 13 — Ingress and Gateway API](13-ingress.md) ·
Next: [Chapter 15 — Storage](15-storage.md)

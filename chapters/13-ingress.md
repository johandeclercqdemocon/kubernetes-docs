# Chapter 13 — Ingress and Gateway API

Chapter 11 ended on a cost problem: one `LoadBalancer` Service is one cloud load balancer,
with a monthly bill and an IP address each. Ingress solves that — one entry point, HTTP
routing to many Services — and introduces problems of its own that the Gateway API exists to
fix.

## Ingress needs a controller

An Ingress object on its own does nothing. It is a *description* of desired routing; a
**controller** must be running to implement it. Install ingress-nginx, Traefik, HAProxy,
Contour, or your cloud's controller, or your Ingress objects sit there inert.

```bash
kubectl get ingressclass
```

```
NAME    CONTROLLER             PARAMETERS   AGE
nginx   k8s.io/ingress-nginx   <none>       22s
```

No IngressClass means no controller, which means nothing will happen. This is the first thing
to check when an Ingress "does not work".

## An Ingress

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: pingd
  annotations:
    nginx.ingress.kubernetes.io/rewrite-target: /$2
spec:
  ingressClassName: nginx
  rules:
    - host: pingd.local
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: pingd
                port:
                  number: 80
    - http:
        paths:
          - path: /api(/|$)(.*)
            pathType: ImplementationSpecific
            backend:
              service:
                name: pingd
                port:
                  number: 80
```

Measured against the running cluster:

```bash
curl -H 'Host: pingd.local' http://localhost:8180/
```

```json
{"service":"pingd","version":"1.0.0","reply":"pong"}
```

```bash
curl http://localhost:8180/api/
```

```json
{"service":"pingd","version":"1.0.0","reply":"pong"}
```

```bash
curl -o /dev/null -w '%{http_code}\n' -H 'Host: nope.local' http://localhost:8180/
```

```
404
```

Host-based routing, path-based routing with a rewrite, and an unmatched host falling through
to the controller's default backend.

### `pathType` matters

- **`Prefix`** — matches on path *segments*. `/api` matches `/api/v1` but **not** `/apifoo`.
  This is what you usually want.
- **`Exact`** — exact string match.
- **`ImplementationSpecific`** — whatever the controller does, which for nginx means regular
  expressions. Required for the capture groups in the rewrite example above.

Getting `Prefix` semantics wrong is a common source of "why does this path not route" — it is
segment-based, not string-based.

## TLS

```yaml
spec:
  tls:
    - hosts: ["pingd.example.com"]
      secretName: pingd-tls
```

The Secret must be `type: kubernetes.io/tls` with `tls.crt` and `tls.key` keys, **in the same
namespace as the Ingress**. Cross-namespace certificate references do not work, and this is a
recurring annoyance when one team owns certificates.

In practice, use **cert-manager** rather than managing certificates by hand:

```yaml
metadata:
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
```

cert-manager sees the annotation, obtains a certificate via ACME, writes the Secret, and
renews it. It is one of the few pieces of cluster software that is unambiguously worth
installing on day one.

## The annotation problem

Look again at the manifest above:

```yaml
annotations:
  nginx.ingress.kubernetes.io/rewrite-target: /$2
```

The Ingress *spec* covers host and path routing and TLS. Everything else — rewrites,
timeouts, request size limits, rate limiting, authentication, CORS, canary weighting, sticky
sessions, custom headers — is **controller-specific annotations**.

The consequences are serious:

- **Not portable.** Switching from ingress-nginx to Traefik means rewriting every annotation.
- **Not validated.** A misspelled annotation is silently ignored. There is no error, no event,
  no warning — the behaviour you asked for just does not happen, and you discover it in
  production.
- **Not typed.** Values are strings, parsed by the controller.
- **Not delegatable.** Annotations are all-or-nothing on one object, so you cannot let an
  application team set a timeout while preventing them changing authentication.

ingress-nginx alone has well over a hundred annotations. This is the reason the Gateway API
exists.

### A security note

Some ingress-nginx annotations allow arbitrary nginx configuration snippets, which has been
the basis of real CVEs — a user with permission to create an Ingress in any namespace could
achieve code execution in the controller, which typically runs with broad cluster access.
Modern versions disable snippets by default (`allow-snippet-annotations: false`). **Keep it
disabled**, keep the controller patched, and treat "can create Ingress" as a privileged
permission (Chapter 23).

## Gateway API

The successor, and the direction the ecosystem is moving. It is a set of CRDs rather than a
built-in type, so it must be installed:

```bash
kubectl apply -f https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.2.1/standard-install.yaml
```

The central idea is **role separation** into three resources:

```yaml
# Infrastructure team: what load balancers exist
apiVersion: gateway.networking.k8s.io/v1
kind: GatewayClass
metadata:
  name: production
spec:
  controllerName: example.com/gateway-controller
---
# Cluster operator: listeners, TLS, and who may attach
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: external
  namespace: infra
spec:
  gatewayClassName: production
  listeners:
    - name: https
      protocol: HTTPS
      port: 443
      tls:
        certificateRefs:
          - name: wildcard-tls
      allowedRoutes:
        namespaces:
          from: Selector
          selector:
            matchLabels: { gateway-access: "true" }
---
# Application team: routing rules, in their own namespace
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: pingd
  namespace: apps
spec:
  parentRefs:
    - name: external
      namespace: infra
  hostnames: ["pingd.example.com"]
  rules:
    - matches:
        - path: { type: PathPrefix, value: /api }
      filters:
        - type: RequestHeaderModifier
          requestHeaderModifier:
            add: [{name: X-Env, value: prod}]
      backendRefs:
        - name: pingd
          port: 80
```

What it fixes:

**Typed, portable configuration.** Header manipulation, redirects, rewrites, request
mirroring and traffic splitting are **fields in the spec**, validated by the API server. A
typo is rejected at apply time rather than silently ignored.

**Real role separation.** The infrastructure team owns the Gateway and controls which
namespaces may attach routes. Application teams own HTTPRoutes in their own namespaces and
cannot change TLS or listener configuration. This is genuinely not expressible with Ingress.

**Native traffic splitting** — canary deployments without annotations:

```yaml
      backendRefs:
        - name: pingd-stable
          port: 80
          weight: 90
        - name: pingd-canary
          port: 80
          weight: 10
```

**Protocols beyond HTTP.** `TCPRoute`, `UDPRoute`, `TLSRoute`, `GRPCRoute` are part of the
model rather than bolted on.

**Cross-namespace references**, governed by `ReferenceGrant` — so a route in one namespace can
target a Service in another, with the target namespace's explicit consent.

### Should you adopt it?

The honest position as of Kubernetes v1.37:

- The **core** (GatewayClass, Gateway, HTTPRoute) is stable (v1) and implemented by Istio,
  Envoy Gateway, Cilium, Traefik, Contour, NGINX Gateway Fabric, Kong and the major cloud
  controllers.
- Some **extended** features remain experimental and implementation-dependent.
- Ingress is **not deprecated** and will keep working indefinitely. There is no forced
  migration.

For a **new** cluster or a new ingress deployment, Gateway API is the better choice. For a
working ingress-nginx setup, migrate when you have a reason — multi-team routing, canary
requirements, or annotation sprawl you can no longer reason about.

## Debugging Ingress

The failure this book hit while being written is instructive. The first requests returned:

```
curl: (56) Recv failure: Connection reset by peer
```

The Ingress existed, the Service had endpoints, the controller was `Running`. The problem was
that **the controller pod had been scheduled onto a node where external traffic never
arrives** — it uses `hostPort: 80`, and only one node had the host port mapped.

```bash
kubectl get pod -n ingress-nginx -l app.kubernetes.io/component=controller \
  -o custom-columns='NAME:.metadata.name,NODE:.spec.nodeName,HOSTPORT:.spec.containers[0].ports[*].hostPort'
```

```
NAME                                        NODE              HOSTPORT
ingress-nginx-controller-596f5b6bcf-c6rzc   k8sbook-worker2   80,443
```

Pinning it to the right node fixed it immediately. The general lesson: **an Ingress problem is
often not an Ingress problem** — it is where the controller runs, whether traffic reaches it,
or whether the backend Service has endpoints.

The debugging order:

```bash
kubectl get ingressclass                                    # is there a controller at all?
kubectl describe ingress pingd                              # events, and resolved backends
kubectl get endpointslice -l kubernetes.io/service-name=pingd  # does the backend have pods?
kubectl logs -n ingress-nginx deploy/ingress-nginx-controller --tail=50
```

`kubectl describe ingress` is particularly useful because it shows whether the controller
resolved your backend Service, and emits events when it could not.

## Try it

Confirm a controller exists:

```bash
kubectl get ingressclass
```

Apply the Ingress and check it was accepted:

```bash
kubectl apply -f examples/manifests/13-ingress.yaml && sleep 10 && kubectl get ingress pingd
```

Host-based routing:

```bash
curl -sS -H 'Host: pingd.local' http://localhost:8180/
```

Path-based routing with a rewrite:

```bash
curl -sS http://localhost:8180/api/
```

An unmatched host falls through to the default backend:

```bash
curl -sS -o /dev/null -w 'status=%{http_code}\n' -H 'Host: nope.local' http://localhost:8180/
```

TLS on the mapped HTTPS port (self-signed, hence `-k`):

```bash
curl -sSk -o /dev/null -w 'https status=%{http_code}\n' -H 'Host: pingd.local' https://localhost:8443/
```

Now see what `describe` tells you about backend resolution:

```bash
kubectl describe ingress pingd | tail -20
```

And prove the annotation problem — a misspelled annotation is accepted silently:

```bash
kubectl annotate ingress pingd nginx.ingress.kubernetes.io/rewrite-targett=/wrong --overwrite
```

No error, no event, no effect. That is the whole argument for Gateway API in one command.

```bash
kubectl annotate ingress pingd nginx.ingress.kubernetes.io/rewrite-targett-
```

## Takeaways

- An Ingress without a controller does nothing. Check `kubectl get ingressclass` first.
- `pathType: Prefix` matches **path segments**, not string prefixes.
- TLS Secrets must be in the same namespace as the Ingress. Use cert-manager.
- **Everything beyond host/path/TLS is controller-specific annotations**: unportable,
  untyped, and **silently ignored when misspelled**.
- Keep ingress-nginx snippet annotations disabled and the controller patched; "can create
  Ingress" is a privileged permission.
- **Gateway API** makes routing typed and validated, separates infrastructure from
  application roles, and supports traffic splitting and non-HTTP protocols natively. Core is
  stable; prefer it for new deployments.
- Ingress failures are often really "the controller is not where the traffic arrives" or "the
  backend Service has no endpoints".

---

Previous: [Chapter 12 — DNS and service discovery](12-dns.md) ·
Next: [Chapter 14 — NetworkPolicy](14-networkpolicy.md)

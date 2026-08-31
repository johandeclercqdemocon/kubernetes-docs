# Chapter 9 — Health and lifecycle

Kubernetes decides two things about your container continuously: should it be restarted, and
should it receive traffic. Those are different questions with different answers, and
conflating them causes cascading outages.

## Three probes

| Probe | Question | On failure | Runs |
|---|---|---|---|
| **startupProbe** | Has it finished booting? | Restart the container | Until first success, then never again |
| **livenessProbe** | Is it wedged? | **Restart the container** | Forever, after startup succeeds |
| **readinessProbe** | Can it serve right now? | **Marked not-ready in EndpointSlices** | Forever |

A precision worth having early: a not-ready pod is not *removed* from its EndpointSlice. It
stays listed with `conditions.ready: false`, and kube-proxy declines to route to it.
Measured during a stalled rollout with three healthy pods and one unready one:

```
true true true false
```

This matters for debugging (Chapter 19): an EndpointSlice containing your pod does not mean
your pod is receiving traffic. Read the conditions, not just the addresses.

All three are executed by the **kubelet on the node**, not by the control plane. Probe
traffic never crosses the network between nodes, and probes keep working during an API
server outage.

```yaml
startupProbe:
  httpGet: { path: /healthz, port: http }
  failureThreshold: 30
  periodSeconds: 1                 # allows up to 30s to boot
livenessProbe:
  httpGet: { path: /healthz, port: http }
  periodSeconds: 10
  failureThreshold: 3              # ~30s of failure before restart
readinessProbe:
  httpGet: { path: /healthz, port: http }
  periodSeconds: 5
  failureThreshold: 2              # ~10s before traffic stops
```

### Liveness and readiness must differ

The single most consequential design decision in this chapter.

**Liveness must not check dependencies.** If your liveness probe queries the database, then
a database blip fails liveness on every replica of every service simultaneously, and
Kubernetes restarts your entire fleet — adding a thundering herd of reconnections to a
database that was about to recover. You have converted a brief dependency wobble into a
full outage, and the restarts make recovery slower.

Liveness answers one question: *is this process stuck in a way only a restart fixes?* For
most services the honest answer is "that essentially never happens", and the correct
liveness probe is a trivial endpoint that returns 200 if the event loop is turning.

**Readiness is where dependencies belong.** If the database is unreachable, this replica
cannot serve, so remove it from the Service — but do not restart it, because restarting will
not bring the database back.

The `pingd` example keeps them separate: `/healthz` checks nothing but itself, `/readyz`
checks the database.

A caveat even for readiness: if *every* replica's readiness depends on a shared dependency,
a dependency outage empties the Service entirely and you serve nothing rather than degraded
responses. Consider whether serving errors is better than serving nothing.

### Startup probes exist for slow starters

Before `startupProbe`, a slow-booting application needed a long `initialDelaySeconds` on its
liveness probe — which then delayed detection of real hangs for the whole life of the
container.

`startupProbe` decouples them: be generous during boot, aggressive afterwards. The
Deployment in this book allows `30 × 1s` for startup, then checks liveness every 10 s.

While a startup probe is running, liveness and readiness are suppressed. This is why the
benign event from Chapter 2 appears on every normal start:

```
Unhealthy   Startup probe failed: dial tcp 10.244.2.7:8000: connect: connection refused
```

The container process exists before the server is listening. The probe retries and
succeeds. **A handful of startup-probe failures during boot is normal**; a `failureThreshold`
exhausted is not.

### Probe types

```yaml
httpGet:  { path: /healthz, port: http, httpHeaders: [{name: Host, value: example.com}] }
tcpSocket: { port: 5432 }
exec:     { command: ["sh","-c","pg_isready -U pingd"] }
grpc:     { port: 9000 }
```

Prefer `httpGet`. `exec` probes fork a process on every check — at 3 replicas × every 10 s
that is trivial, at 3000 replicas it is a measurable load on your nodes, and exec probes
have been the cause of real cluster-wide CPU problems. `tcpSocket` only proves something is
listening, which is weak evidence.

Use a **named port** (`port: http`) rather than a number, so changing the container port does
not silently break the probe.

## Configuration mistakes that cause outages

**Timeout too short.** `timeoutSeconds` defaults to **1 second**. A service that occasionally
takes 1.2 s to answer its health endpoint under load will be restarted, under load, making
the load worse. This is a classic self-inflicted cascading failure.

**Period plus threshold too aggressive.** `periodSeconds: 5, failureThreshold: 1` restarts on
a single blip. Give real applications room: `failureThreshold: 3` at minimum.

**A liveness endpoint that does real work.** If `/healthz` queries the database, renders a
template, or acquires a contended lock, it will fail under exactly the load conditions where
restarting is worst.

**Probes with no resource headroom.** A CPU-throttled container (Chapter 8) fails its own
liveness probe because it cannot get scheduled to answer it, gets restarted, and starts the
cycle again.

## Graceful shutdown, and the race nobody expects

When a pod is deleted, two things happen **in parallel**:

1. The kubelet sends **SIGTERM** to the containers and starts the
   `terminationGracePeriodSeconds` clock.
2. The endpoint controller removes the pod from EndpointSlices, and then every kube-proxy on
   every node must update its rules.

Step 2 is **eventually consistent and not instant**. For a short window — typically tens to
hundreds of milliseconds, longer on large clusters — the pod is shutting down while nodes
are still routing new connections to it.

The result is dropped requests during every deployment, and it is the most common cause of
"we get 502s during rollouts" when everything else looks correct.

The fix is to make the pod keep serving for a moment after receiving SIGTERM:

```yaml
lifecycle:
  preStop:
    exec:
      command: ["sh", "-c", "sleep 5"]
```

The `preStop` hook runs **before** SIGTERM is sent, and the container keeps serving during
it. Five seconds is enough for endpoint propagation nearly everywhere. It feels wrong —
deliberately sleeping — and it is the standard, correct fix.

Better still, if your application can: fail readiness immediately on SIGTERM, keep serving
in-flight and new requests for a few seconds, then exit. That is what the `sleep` approximates
without application changes.

The full sequence:

```
pod deleted
  ├─► preStop hook runs (container still serving)   ─┐
  └─► endpoint removal propagates to kube-proxy      ─┘ overlap here is the point
      SIGTERM to PID 1
      application drains in-flight work
      application exits  →  done
      ...or grace period expires  →  SIGKILL (exit 137)
```

```yaml
spec:
  terminationGracePeriodSeconds: 30
```

Set this longer than your longest reasonable request. If your pods always take exactly the
full grace period to terminate, they are being SIGKILLed — which means PID 1 is not handling
SIGTERM, and everything the Docker book says about exec-form `CMD` and signal handlers
applies unchanged.

## Restart backoff

A container that keeps failing is restarted with exponential backoff: 10 s, 20 s, 40 s,
capped at 5 minutes. During the wait the pod shows `CrashLoopBackOff` — which is a *timer*,
not an error condition (Chapter 17).

The backoff resets after the container has run successfully for 10 minutes.

## Pod readiness gates

Occasionally you need readiness to depend on something outside the container — a cloud load
balancer having actually registered the pod, for instance:

```yaml
spec:
  readinessGates:
    - conditionType: "example.com/lb-registered"
```

The pod is not `Ready` until some controller sets that condition to `True`. Cloud load
balancer controllers use this to prevent traffic being sent before the external LB knows
about the pod — a real source of deployment errors on AWS in particular.

## Try it

Look at the probe configuration on the running example:

```bash
kubectl get deployment pingd -o jsonpath='startup={.spec.template.spec.containers[0].startupProbe.failureThreshold}x{.spec.template.spec.containers[0].startupProbe.periodSeconds}s liveness={.spec.template.spec.containers[0].livenessProbe.periodSeconds}s readiness={.spec.template.spec.containers[0].readinessProbe.periodSeconds}s{"\n"}'
```

```
startup=30x1s liveness=10s readiness=5s
```

Confirm all three replicas are in the Service:

```bash
kubectl get endpointslice -l kubernetes.io/service-name=pingd -o jsonpath='{.items[0].endpoints[*].conditions.ready}{"\n"}'
```

```
true true true
```

Now point readiness at a path that does not exist, and watch what a broken rollout looks
like when readiness is doing its job:

```bash
kubectl patch deployment pingd --type=json -p='[{"op":"replace","path":"/spec/template/spec/containers/0/readinessProbe/httpGet/path","value":"/nope"}]'
```

```bash
sleep 25 && kubectl get pods -l app=pingd
```

```
pingd-5975cc6496-5sxvh   1/1   Running   0   9m6s
pingd-5975cc6496-6hzpx   1/1   Running   0   9m11s
pingd-5975cc6496-9bsdg   1/1   Running   0   9m2s
pingd-68894bbf58-f68kq   0/1   Running   0   25s     ← new pod, never ready
```

The new pod is `Running` but `0/1`. Because `maxUnavailable: 0`, the three old pods are
untouched and still serving — the rollout **stalled instead of taking the service down**.
Look at the endpoint conditions:

```bash
kubectl get endpointslice -l kubernetes.io/service-name=pingd -o jsonpath='{.items[0].endpoints[*].conditions.ready}{"\n"}'
```

```
true true true false
```

Four endpoints, three routable. The broken pod is listed and excluded. Undo:

```bash
kubectl rollout undo deployment/pingd && kubectl rollout status deployment/pingd
```

Finally, watch a graceful termination and time it:

```bash
POD=$(kubectl get pod -l app=pingd -o jsonpath='{.items[0].metadata.name}') && time kubectl delete pod $POD
```

Well under the 30 s grace period, because `pingd` handles SIGTERM. A pod that always takes
exactly `terminationGracePeriodSeconds` is being killed, not stopping.

## Takeaways

- Three probes, three jobs. The kubelet runs them locally.
- **Liveness must not check dependencies.** A dependency-checking liveness probe turns a
  database blip into a fleet-wide restart storm.
- Readiness is where dependencies belong — but if every replica depends on one thing, an
  outage empties the Service entirely.
- `startupProbe` lets you be generous at boot and aggressive afterwards. A few startup-probe
  failures during boot are normal.
- **`timeoutSeconds` defaults to 1 second.** This restarts healthy-but-slow services under
  load.
- Prefer `httpGet` with a **named port**; `exec` probes fork a process every period.
- Endpoint removal and SIGTERM happen **in parallel**, so a terminating pod still receives
  new connections briefly. A `preStop` sleep of ~5 s is the standard fix for rollout 502s.
- Pods always taking the full grace period means PID 1 is not handling SIGTERM.
- `CrashLoopBackOff` is a backoff timer, not a state.

---

Previous: [Chapter 8 — Resources, requests and QoS](08-resources-and-qos.md) ·
Next: [Chapter 10 — The other workload kinds](10-other-workloads.md)

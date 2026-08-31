# Chapter 5 — Pods

The Pod is the atom. Kubernetes does not schedule containers; it schedules Pods. Every
higher-level workload type — Deployment, StatefulSet, Job, DaemonSet — exists to create and
manage Pods, and every one of them contains a pod template.

## What a Pod is

**A group of containers that share a network namespace, IPC namespace and storage volumes,
scheduled together on one node, sharing a lifecycle.**

Concretely, containers in a Pod:

- share one **IP address** and one port space, so they reach each other on `localhost`;
- can share **volumes**;
- are always **co-located** on the same node;
- are created and destroyed **together**.

They do *not* share a filesystem root (each has its own image) and, by default, do *not*
share a PID namespace.

Measured on a two-container Pod. The sidecar reaching the server over `localhost`:

```bash
kubectl exec multi -c sidecar -- wget -qO- http://localhost:8080/
```

```
prepared by init at 12:21:15
```

Same network namespace, no Service involved. But their process views are separate:

```bash
kubectl exec multi -c server  -- ps -o pid,comm
kubectl exec multi -c sidecar -- ps -o pid,comm
```

```
server:   PID 1 httpd
sidecar:  PID 1 sleep
```

Each container is PID 1 in its own namespace. Enable sharing explicitly with
`shareProcessNamespace: true` when you need one container to see or signal another's
processes — useful for debugging sidecars, and a security consideration since it also lets
them read each other's `/proc`.

### Why the Pod exists at all

The abstraction is not arbitrary. Some things genuinely need to be co-located with shared
network and storage — a log shipper reading a volume the app writes, a proxy terminating TLS
on the app's behalf, a credential refresher writing to a shared tmpfs. Making the *pod*
rather than the container the schedulable unit means "these processes must live together"
is expressible.

The Docker book's advice about one process per container becomes, here: **one concern per
container, one cohesive unit per Pod.** If two containers do not need to share a network
namespace or a volume, they probably want to be separate Pods.

## The infrastructure container

Every Pod has a hidden `pause` container that holds the namespaces. It does nothing but
sleep and reap. Application containers join *its* namespaces, which is why individual
containers in a Pod can restart without the Pod losing its IP address.

That detail explains an important behaviour: a crash-looping container does not change the
Pod's IP, and the Pod is not rescheduled. Restarts happen in place, on the same node.

## You rarely create Pods directly

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: standalone
spec:
  containers:
    - name: app
      image: pingd:latest
```

This works and you should almost never do it. A bare Pod has **nothing watching it**. If it
dies, it stays dead. If its node dies, it is gone. There is no controller with a desired
state that includes it.

Use a Deployment (Chapter 6) for services, a Job for batch work, a DaemonSet for per-node
agents. Bare Pods are for debugging and one-off experiments — which is exactly what
`kubectl run --rm -it` is for:

```bash
kubectl run tmp --rm -it --restart=Never --image=alpine -- sh
```

## Init containers

Containers that run to completion, **in order**, before any app container starts. If one
fails, the Pod restarts it according to the restart policy; app containers do not start
until all init containers have succeeded.

```yaml
spec:
  initContainers:
    - name: setup
      image: busybox:1.37
      command: ['sh','-c','echo "prepared at $(date -u +%T)" > /work/index.html']
      volumeMounts:
        - name: shared
          mountPath: /work
  containers:
    - name: server
      image: busybox:1.37
      command: ['sh','-c','httpd -f -p 8080 -h /work']
      volumeMounts:
        - name: shared
          mountPath: /work
```

The app container sees the init container's output:

```bash
kubectl exec multi -c server -- cat /work/index.html
```

```
prepared at 12:21:15
```

Legitimate uses: waiting for a dependency to be reachable, running database migrations,
fetching configuration or secrets, setting file ownership on a volume, cloning a git repo.

Two properties make them useful beyond "run something first": init containers can use a
**different image** with tools the app image lacks (keeping the runtime image minimal), and
they run with the Pod's volumes already mounted.

The waiting pattern, which replaces application-level startup retries in some designs:

```yaml
initContainers:
  - name: wait-for-db
    image: busybox:1.37
    command: ['sh','-c','until nc -z db 5432; do echo waiting; sleep 2; done']
```

Use it, but do not let it replace retry logic in the application (Chapter 9) — it helps at
startup and does nothing when the database restarts later.

## Sidecar containers

A sidecar runs *alongside* the app for the Pod's lifetime. The classic problem was that
ordinary containers all start in parallel with no ordering, and a sidecar that must be ready
first — a proxy, a credential agent — created race conditions.

Since v1.29 (stable in v1.33), sidecars are **init containers with `restartPolicy:
Always`**:

```yaml
spec:
  initContainers:
    - name: proxy
      image: envoyproxy/envoy:v1.32-latest
      restartPolicy: Always       # ← this makes it a sidecar
  containers:
    - name: app
      image: pingd:latest
```

That gives you what everyone wanted: the sidecar starts *before* app containers, keeps
running alongside them, and shuts down *after* them. It also fixes the long-standing Job
problem where a never-exiting sidecar prevented the Job from ever completing.

If you are reading older material describing sidecars as ordinary containers with startup
races and Job hangs, this is the fix, and it is worth adopting.

## The Pod lifecycle

`status.phase` has five values, and it is coarser than people expect:

| Phase | Meaning |
|---|---|
| `Pending` | Accepted, but not all containers running. Scheduling, pulling images, or init containers running. |
| `Running` | Bound to a node, all containers created, at least one running. |
| `Succeeded` | All containers terminated successfully; will not restart. |
| `Failed` | All containers terminated, at least one in failure. |
| `Unknown` | The node cannot be reached. |

**`Running` does not mean working.** It means containers exist. Readiness (Chapter 9) is a
separate thing, which is why `kubectl get pods` shows both `STATUS` and `READY`:

```
NAME                     READY   STATUS    RESTARTS   AGE
pingd-5975cc6496-fmfvv   1/1     Running   0          2m
```

`READY 1/1` is the number that determines whether traffic is sent. A pod can sit at `0/1
Running` indefinitely, healthy-looking and receiving nothing.

The real detail is in `status.containerStatuses`, and `describe` is the fastest way to it:

```bash
kubectl describe pod POD | sed -n '/Containers:/,/Conditions:/p'
```

Look for `State`, `Last State` (the previous run, if it restarted), `Reason`, and `Exit
Code`. Chapter 17 works through these.

## Restart policy

```yaml
spec:
  restartPolicy: Always      # default. Also: OnFailure, Never
```

It applies to **containers within the Pod**, not to the Pod itself — this trips people up.
`restartPolicy: Always` means the kubelet restarts a failed container **in place**, on the
same node, with the same Pod IP. Nothing reschedules.

Deployments require `Always`. Jobs use `OnFailure` or `Never`.

Restarts use exponential backoff: 10 s, 20 s, 40 s, up to 5 minutes. That backoff is what
`CrashLoopBackOff` is (Chapter 17) — the Pod is not in an error state, it is *waiting*
before the next attempt.

## Resources, probes, security context

Every Pod should set resource requests and limits (Chapter 8), probes (Chapter 9) and a
security context (Chapter 23). They belong in this chapter's mental model but each has its
own chapter, so here is only the shape:

```yaml
spec:
  securityContext:              # pod-level
    runAsNonRoot: true
    runAsUser: 10001
    fsGroup: 10001
    seccompProfile:
      type: RuntimeDefault
  containers:
    - name: api
      resources:
        requests: { cpu: 50m, memory: 96Mi }
        limits:   { cpu: 500m, memory: 192Mi }
      livenessProbe:
        httpGet: { path: /healthz, port: http }
      securityContext:          # container-level, overrides pod-level
        allowPrivilegeEscalation: false
        readOnlyRootFilesystem: true
        capabilities: { drop: ["ALL"] }
```

Pod-level `securityContext` applies to all containers; container-level overrides it for one.
Some fields exist only at one level — `fsGroup` is pod-only, `capabilities` and
`readOnlyRootFilesystem` are container-only — which is a common source of "why is this field
rejected".

## Scheduling controls

Where a Pod runs, in increasing order of expressiveness:

```yaml
spec:
  nodeSelector:                    # simplest: exact label match
    disktype: ssd
```

```yaml
spec:
  affinity:
    nodeAffinity:                  # expressive: operators, soft preferences
      requiredDuringSchedulingIgnoredDuringExecution:
        nodeSelectorTerms:
          - matchExpressions:
              - key: topology.kubernetes.io/zone
                operator: In
                values: ["eu-west-1a", "eu-west-1b"]
    podAntiAffinity:               # spread replicas across nodes
      preferredDuringSchedulingIgnoredDuringExecution:
        - weight: 100
          podAffinityTerm:
            labelSelector:
              matchLabels: { app: pingd }
            topologyKey: kubernetes.io/hostname
```

Note `IgnoredDuringExecution` in those names: the rules apply **at scheduling time only**.
If a node's labels change afterwards, nothing moves. There is no "RequiredDuringExecution"
— it has been proposed for years and does not exist.

**Tolerations** let a Pod schedule onto a *tainted* node. Taints repel; tolerations permit:

```bash
kubectl taint nodes k8sbook-worker2 gpu=true:NoSchedule
```

```yaml
spec:
  tolerations:
    - key: gpu
      operator: Equal
      value: "true"
      effect: NoSchedule
```

Control-plane nodes are tainted by default, which is why your workloads land on workers
without you asking. Chapter 20 covers the `NoExecute` taints the node controller applies
during failures, and Chapter 25 covers `topologySpreadConstraints`, which is the modern and
better answer to spreading replicas.

## Try it

Build a Pod that demonstrates all three sharing behaviours:

```bash
kubectl apply -f examples/manifests/05-multi-container.yaml
```

```bash
kubectl wait --for=condition=Ready pod/multi --timeout=60s
```

Shared network namespace — the sidecar reaches the server on localhost:

```bash
kubectl exec multi -c sidecar -- wget -qO- http://localhost:8080/
```

Separate PID namespaces — each container is its own PID 1:

```bash
kubectl exec multi -c server -- ps -o pid,comm; kubectl exec multi -c sidecar -- ps -o pid,comm
```

Shared volume, written by the init container before either app container started:

```bash
kubectl exec multi -c sidecar -- cat /work/index.html
```

See the whole lifecycle including init containers:

```bash
kubectl describe pod multi | sed -n '/Init Containers:/,/Conditions:/p'
```

Prove a bare Pod has nothing watching it:

```bash
kubectl delete pod multi && sleep 5 && kubectl get pod multi
```

```
Error from server (NotFound): pods "multi" not found
```

Gone, permanently. Compare with a Deployment's pod, which returns in under a second
(Chapter 2).

Finally, watch scheduling constraints take effect:

```bash
kubectl taint nodes k8sbook-worker2 demo=true:NoSchedule
```

```bash
kubectl run tainted-test --image=alpine --restart=Never --overrides='{"spec":{"nodeSelector":{"kubernetes.io/hostname":"k8sbook-worker2"}}}' -- sleep 60
```

```bash
sleep 3 && kubectl get pod tainted-test -o wide && kubectl describe pod tainted-test | grep -A3 Events
```

`Pending`, with a `FailedScheduling` event naming the taint. Clean up:

```bash
kubectl delete pod tainted-test --force --grace-period=0 2>/dev/null; kubectl taint nodes k8sbook-worker2 demo-
```

## Takeaways

- A Pod is containers sharing a network namespace, IPC and volumes, co-scheduled and
  co-terminated. They do **not** share a PID namespace unless you ask.
- The `pause` container holds the namespaces, which is why a container can restart in place
  without the Pod losing its IP.
- Do not create bare Pods for real workloads — nothing watches them.
- Init containers run to completion, in order, before app containers, and may use a
  different image with tools the runtime image lacks.
- **Sidecars are now init containers with `restartPolicy: Always`** — correct ordering, and
  Jobs can finally complete.
- `Running` ≠ working. `READY 1/1` is what determines traffic.
- `restartPolicy` restarts containers **in place** on the same node; it never reschedules.
  `CrashLoopBackOff` is the backoff timer, not an error state.
- Affinity and node selectors apply **at scheduling time only** — nothing moves when labels
  change later.

---

Previous: [Chapter 4 — kubectl and the resource model](04-kubectl-and-resources.md) ·
Next: [Chapter 6 — Deployments and ReplicaSets](06-deployments.md)

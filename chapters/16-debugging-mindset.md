# Chapter 16 — The debugging mindset

The Docker book had four layers to localise a problem. Kubernetes has five, and the extra one
— the control plane — can itself be the fault. That is the whole difficulty: a pod that will
not serve traffic might be failing for reasons in the image, the scheduler, RBAC, an admission
webhook, a probe, a NetworkPolicy, a taint, or a Service selector, and the symptom looks
identical in most of those cases.

The way through is the same as before: **localise the layer before investigating**.

## The five layers

| Layer | Question | First command |
|---|---|---|
| **1. Object** | Does the desired state say what I think? | `kubectl get -o yaml`, `kubectl describe` |
| **2. Scheduling** | Was it placed on a node? | `kubectl get pod -o wide`, events |
| **3. Container** | Did the image pull and the process start? | `kubectl logs`, container statuses |
| **4. Network** | Can it be reached, and can it reach? | endpoints, DNS, policy |
| **5. Cluster** | Is the platform itself healthy? | node status, control plane pods |

Most wasted time is spent in the wrong layer. "The API can't reach the database" is a layer-4
hypothesis, and about a third of the time the truth is layer 3 (the database pod is
crash-looping) or layer 1 (the Service selector does not match).

## The triage sequence

Four commands, ninety seconds, before forming any theory.

**1. What state is it in?**

```bash
kubectl get pods -o wide
```

The `STATUS` and `READY` columns are different facts. `Running` with `0/1` ready means the
process started and readiness fails — a completely different problem from `Pending`.

**2. What does the object say happened?**

```bash
kubectl describe pod POD
```

This is the highest-value single command in Kubernetes debugging. It gives you the container
statuses, the resolved configuration, the conditions, **and the events** — and the events are
usually the answer outright.

**3. What did the application say?**

```bash
kubectl logs POD
```

```bash
kubectl logs POD --previous
```

**4. What has the cluster been doing?**

```bash
kubectl get events --sort-by=.lastTimestamp | tail -20
```

Remember from Chapter 4 that events expire after about an hour and are unsorted by default.

## Read the status columns precisely

The `STATUS` column is a composite that the API does not store as a single field, and knowing
what each value means saves a lot of guessing:

| STATUS | Layer | Meaning |
|---|---|---|
| `Pending` | 2 | Not scheduled, or scheduled and still pulling/initialising |
| `ContainerCreating` | 3 | Scheduled; pulling image, mounting volumes |
| `Running` + `1/1` | — | Working |
| `Running` + `0/1` | 3/4 | Process up, **readiness failing** |
| `ImagePullBackOff` / `ErrImagePull` | 3 | Cannot fetch the image |
| `CreateContainerConfigError` | 1 | Missing ConfigMap/Secret reference |
| `CrashLoopBackOff` / `Error` | 3 | Container keeps exiting |
| `Terminating` | — | Being deleted; stuck here means a finalizer or a grace period |
| `Evicted` | 5 | Node pressure removed it (Chapter 20) |
| `OOMKilled` | 3 | Memory limit — but see Chapter 8, it may say `Error` |

Two of these deserve care.

**`Pending` splits into two very different problems.** Look for a node assignment:

```bash
kubectl get pod POD -o jsonpath='{.spec.nodeName}{"\n"}'
```

Empty means the **scheduler** could not place it — a `FailedScheduling` event will say why.
Non-empty means it *is* scheduled and the kubelet is working on it — image pull, volume
mount, init containers. Same status, opposite investigations.

**`Running` never means "working".** It means containers exist. `READY` is what determines
traffic.

## Events are the narration

Events explain what controllers decided, and their messages are unusually specific. A real
one from this cluster:

```
Warning  FailedScheduling  8s  default-scheduler
  0/3 nodes are available: 1 node(s) had untolerated taint(s),
  2 Insufficient cpu, 2 Insufficient memory.
  preemption: 0/3 nodes are available: 3 Preemption is not helpful for scheduling.
```

That single message tells you the cluster size, that one node was excluded by a taint, that
the other two lacked both CPU and memory, and that preemption would not help. There is very
little left to guess.

Scoped to one object:

```bash
kubectl events --for pod/POD
```

```bash
kubectl get events --field-selector involvedObject.name=POD,type=Warning
```

## `kubectl describe` reading order

`describe` output is long. Read it in this order:

1. **`Status:`** and **`Conditions:`** — the summary and what is unsatisfied.
2. **`Containers:` → `State` / `Last State`** — including `Reason` and `Exit Code`. `Last
   State` is the run that failed.
3. **`Events:`** at the bottom — usually the actual answer.
4. **`Node:`**, **`IP:`**, **`Mounts:`**, **`Environment:`** — to confirm what it actually got
   versus what you meant.

That fourth point catches an unreasonable share of problems: the gap between the manifest you
edited and the object that is running. A different namespace, an un-applied change, a
`kustomize` overlay you forgot, an HPA overriding replicas.

## Ask what changed

Same discipline as the Docker book, and it wins as often:

```bash
kubectl rollout history deployment/pingd
```

```bash
kubectl get events --sort-by=.lastTimestamp -A | tail -30
```

```bash
kubectl get pods -A --sort-by=.metadata.creationTimestamp | tail -10
```

- **Was it ever working?** Never-worked points at layers 1 and 2; stopped-working at 3, 4 and
  5.
- **Does the previous revision work?** `kubectl rollout undo` is both a fix and a bisection.
- **Is it one pod or all of them?** One pod means a node or a scheduling problem; all pods
  means the image, config or a dependency.
- **Is it one namespace or the cluster?** Cluster-wide points at layer 5 — CoreDNS, a webhook,
  the control plane.

That third question is worth making a habit. Compare a failing pod's node against a healthy
one:

```bash
kubectl get pods -o wide -l app=pingd
```

If every failure is on one node, you have a node problem (Chapter 21), not an application
problem.

## Reduce the system

Strip away layers until the failure disappears:

```bash
kubectl run debug --rm -it --restart=Never --image=nicolaka/netshoot -- bash
```

A fresh pod in the same namespace tests DNS, policy and Service routing *without* your
application. If it can reach the Service and your pod cannot, the problem is inside your pod.

```bash
kubectl port-forward deploy/pingd 8080:8000
```

Bypasses Service, kube-proxy, Ingress and DNS entirely — straight to a pod. If this works and
your Service does not, you have localised to layer 4 and specifically to the Service.

```bash
kubectl run direct --rm -it --restart=Never --image=curlimages/curl -- curl -sS http://POD_IP:8000/
```

Pod IP directly, bypassing the Service but not the network. Between these three you can
usually pin the failure to a single hop.

## Rules of thumb

**Empty logs mean the process never wrote to stdout** — it died before starting, it logs to a
file, or output is buffered. Everything the Docker book said about `PYTHONUNBUFFERED` applies.

**A Service with no ready endpoints is the single most common networking cause.** Check it
before anything else (Chapter 19).

**If it worked before the deploy, it is the deploy.** Roll back first, investigate after. The
cluster is not a debugging environment when users are affected.

**Check the namespace.** A shocking proportion of "the resource doesn't exist" is
`kubectl` pointed at `default`.

**Check the context.** The rest is `kubectl` pointed at the wrong cluster.

**When one pod is broken and its siblings are fine, compare them.** Same image, same config,
different outcome means the difference is the node, the scheduling, or local state:

```bash
diff <(kubectl get pod GOOD -o yaml) <(kubectl get pod BAD -o yaml) | head -40
```

## What the rest of Part IV covers

| Symptom | Chapter |
|---|---|
| Pending, ImagePullBackOff, CrashLoopBackOff, config errors | [17](17-pods-wont-run.md) |
| I need a shell, or there is no shell | [18](18-getting-inside.md) |
| Cannot reach a Service; DNS; policy | [19](19-debugging-networks.md) |
| OOM, throttling, evictions, preemption | [20](20-resources-and-eviction.md) |
| NotReady nodes, taints, control plane | [21](21-nodes-and-control-plane.md) |
| I have an error message and want the answer | [22](22-cookbook.md) |

## Takeaways

- Five layers: object, scheduling, container, network, cluster. Localise before investigating.
- Triage: `get pods -o wide` → `describe` → `logs` (and `--previous`) → `events`.
- **`describe` is the highest-value command** because it includes events.
- `Pending` with **no** `nodeName` is a scheduler problem; with a `nodeName` it is a kubelet
  problem. Same word, opposite investigations.
- `Running` ≠ working. `READY` decides traffic.
- `FailedScheduling` messages are unusually specific — read them literally.
- Reduce the system: a fresh netshoot pod, `port-forward` to bypass Services, direct pod IP.
- One pod broken and siblings fine means node or scheduling; all broken means image or config;
  cluster-wide means layer 5.
- Check the namespace, then the context.

---

Previous: [Chapter 15 — Storage](15-storage.md) ·
Next: [Chapter 17 — Pods that won't run](17-pods-wont-run.md)

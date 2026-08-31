# Chapter 2 — Declarative reconciliation

If you take one idea from this book, take this one. Everything Kubernetes does — rollouts,
self-healing, autoscaling, certificate management, the entire operator ecosystem — is the
same mechanism applied to different objects.

## `kubectl apply` is not a command

The instinct carried over from Docker is that `kubectl apply` *does* something, the way
`docker run` does. It does not. It writes a record.

```bash
kubectl apply -f 01-deployment.yaml
```

That request means: "the cluster should contain a Deployment named `pingd` with these
properties." The API server validates it, stores it in etcd, and returns. **No pod has been
created.** Nothing has been scheduled. The API server does not create pods; it does not
know how.

What happens next is that several independent controllers, each watching for changes to
things they care about, notice and act:

1. The **Deployment controller** sees a Deployment with no matching ReplicaSet, and creates
   one.
2. The **ReplicaSet controller** sees a ReplicaSet wanting 3 pods and finding 0, and
   creates 3 Pod objects — still just records, with no node assigned.
3. The **scheduler** sees Pods with an empty `spec.nodeName`, picks nodes for them, and
   writes the choice back.
4. The **kubelet** on each chosen node sees a Pod assigned to it, and tells containerd to
   pull the image and start the container.
5. The kubelet reports status back; the ReplicaSet and Deployment controllers update their
   own status.

Five actors, each doing one job, none of them talking to each other directly. They
communicate only by reading and writing objects through the API server. That is the
architecture, and it explains almost every behaviour you will encounter.

## The control loop

Each controller runs the same loop, forever:

```
observe desired state  (the spec, from the API)
observe actual state   (the status, from the API or the world)
if they differ: take one step to close the gap
repeat
```

Three properties follow, and they are worth stating because they explain a lot of otherwise
surprising behaviour.

**It is level-triggered, not edge-triggered.** A controller does not react to "a pod was
deleted". It periodically observes that there are 2 pods where there should be 3, and
creates one. If it misses an event, restarts, or is disconnected for a minute, it recovers
by simply looking again. This is why Kubernetes is robust to controllers crashing — there
is no queue of missed work to replay, only a current difference to close.

**It is continuous.** Reconciliation does not stop after the rollout finishes. Delete a pod
a week later and it comes back, because the loop never stopped comparing.

**It converges rather than executing steps.** If you change replicas from 3 to 10 and then,
two seconds later, to 5, you do not get 10 pods and then a scale-down. The controller reads
current desired state each time round, so it converges on 5.

### Measured

Delete a pod from a healthy 3-replica Deployment and time the response:

```
replacement pod object created after:  0.44 s
all replicas Ready again after:        4.81 s
```

Under half a second from deletion to a new Pod object existing. Nothing was instructed to
do that; the ReplicaSet controller observed 2 where it wanted 3.

The events record each actor's contribution:

```
Killing            pingd-...-fmfvv    Stopping container api
SuccessfulCreate    pingd-5975cc6496   Created pod: pingd-...-67prp
Pulled              pingd-...-67prp    Container image "pingd:latest" already present
Created             pingd-...-67prp    Container created
Started             pingd-...-67prp    Container started
Unhealthy           pingd-...-67prp    Startup probe failed: connect: connection refused
```

Note the last line. A failed startup probe during boot is **normal** — the container is up
before the server is listening, and the probe retries. Chapter 9 explains why that is what
`startupProbe` is for, and Chapter 17 explains how to tell this benign case from a real
failure.

## Spec and status

Every Kubernetes object has the same shape:

```yaml
apiVersion: apps/v1     # which API group and version
kind: Deployment        # what type of thing
metadata:               # name, namespace, labels, annotations, uid
  name: pingd
spec:                   # what you want — you write this
  replicas: 3
status:                 # what is — controllers write this
  readyReplicas: 3
```

**You write `spec`. Controllers write `status`.** Editing `status` by hand is meaningless;
the controller will overwrite it on the next pass. This division is absolute and is the
clearest signal of whether a field is an input or an output.

Look at what the API server added to a Deployment you never wrote:

```bash
kubectl get deployment pingd -o json | jq '.metadata | keys, .status | keys'
```

```
metadata: annotations, creationTimestamp, generation, labels, name,
          namespace, resourceVersion, uid
status:   availableReplicas, conditions, observedGeneration, readyReplicas,
          replicas, terminatingReplicas, updatedReplicas
```

Four of those are load-bearing:

**`uid`** — a unique identifier for this object instance. Delete and recreate a Deployment
with the same name and it gets a new uid; that is how controllers know it is a different
object.

**`generation` and `observedGeneration`** — `generation` increments each time you change
the *spec*. `observedGeneration` is how far the controller has got. When they are equal, the
controller has seen your latest change. When they differ, it has not caught up yet, which
is exactly what `kubectl rollout status` is waiting for.

**`resourceVersion`** — changes on every write, and is the basis of optimistic concurrency.
Measured, adding an annotation:

```
before: 1006
after:  1211
```

When you `kubectl edit`, the version you fetched is sent back; if someone else wrote in the
meantime, the API server rejects your update with a conflict rather than silently losing
their change. It is also what `watch` uses to resume a stream without missing events.

**`conditions`** — the standard way controllers report. Not a single status field but a list
of independent assertions:

```bash
kubectl get deployment pingd -o jsonpath='{range .status.conditions[*]}{.type}={.status} reason={.reason}{"\n"}{end}'
```

```
Available=True    reason=MinimumReplicasAvailable
Progressing=True  reason=NewReplicaSetAvailable
```

Conditions are the first thing to read when something is wrong, on any object type. They
carry a `reason` and usually a `message` explaining precisely what a controller is unhappy
about.

## Ownership and cascading deletion

Objects created by controllers carry an `ownerReferences` field pointing at their creator:

```bash
kubectl get pods -l app=pingd -o jsonpath='{range .items[*]}{.metadata.name}{"  owner="}{.metadata.ownerReferences[0].kind}{"/"}{.metadata.ownerReferences[0].name}{"\n"}{end}'
```

```
pingd-5975cc6496-fmfvv  owner=ReplicaSet/pingd-5975cc6496
pingd-5975cc6496-mzrzk  owner=ReplicaSet/pingd-5975cc6496
pingd-5975cc6496-wxcdc  owner=ReplicaSet/pingd-5975cc6496
```

And the ReplicaSet in turn is owned by the Deployment. This chain is what makes `kubectl
delete deployment pingd` remove the pods too: the **garbage collector** is another
controller, which deletes objects whose owner no longer exists.

It also explains a class of confusion. Delete a ReplicaSet directly and the Deployment
controller creates a new one within a second, because from its point of view a required
object went missing. You cannot delete the middle of an ownership chain and have it stay
deleted — you have to change the desired state at the top.

To deliberately keep the children:

```bash
kubectl delete deployment pingd --cascade=orphan
```

The pods survive, ownerless, and nothing manages them. Occasionally useful in a migration;
a mess if done by accident.

## Why things sometimes "come back"

A short list of behaviours that make sense only through this lens:

- **A deleted pod returns.** Its ReplicaSet still wants it.
- **An edited pod reverts.** You edited a pod the ReplicaSet manages; it does not reconcile
  pod *contents*, but any replacement comes from the template, so your change vanishes at
  the next restart. Edit the Deployment, not the pod.
- **A scaled Deployment un-scales.** Something else — an HPA (Chapter 25), or a GitOps
  controller (Chapter 27) — also has an opinion about `replicas` and is writing it back.
  Two controllers fighting over one field is a genuine and confusing failure mode.
- **A deleted namespace hangs in `Terminating`.** A finalizer on some object inside it has
  not completed. Chapter 21 covers finalizers.
- **Your change had no effect.** Check `generation` versus `observedGeneration`: if they
  differ, no controller has processed it, which usually means the responsible controller is
  down.

## Imperative commands still exist

`kubectl create`, `kubectl scale`, `kubectl set image`, `kubectl expose` all work by
writing objects, and they are fine for experimentation:

```bash
kubectl scale deployment pingd --replicas=5
```

But they leave no record anywhere except the cluster. The next `kubectl apply` from your
manifests will set replicas back to 3, silently. That is not a bug — it is the system doing
exactly what you asked, twice, with different desired states.

The production discipline is: **manifests in git are the desired state, and the cluster is
a derivative.** Chapter 27 makes that concrete with GitOps. For now, treat imperative
commands as debugging tools, not deployment tools.

A useful middle ground is generating manifests rather than applying them:

```bash
kubectl create deployment pingd --image=pingd:latest --dry-run=client -o yaml > deployment.yaml
```

## Try it

Watch the ownership chain assemble itself from a single object:

```bash
kubectl apply -f examples/manifests/01-deployment.yaml
```

```bash
kubectl get deployment,replicaset,pods -l app=pingd
```

You created one object; three kinds exist.

Prove that controllers are continuous rather than one-shot. Delete a pod and watch:

```bash
kubectl delete pod -l app=pingd --field-selector status.phase=Running --wait=false | head -1; kubectl get pods -l app=pingd -w
```

(Ctrl-C when it settles.) Now try to defeat it by deleting the ReplicaSet:

```bash
kubectl delete rs -l app=pingd && sleep 2 && kubectl get rs -l app=pingd
```

A new ReplicaSet, seconds old. The Deployment still wants one.

Watch `generation` track your changes:

```bash
kubectl get deployment pingd -o jsonpath='gen={.metadata.generation} observed={.status.observedGeneration}{"\n"}'
```

```bash
kubectl scale deployment pingd --replicas=4 && kubectl get deployment pingd -o jsonpath='gen={.metadata.generation} observed={.status.observedGeneration}{"\n"}'
```

Then see the imperative/declarative conflict for yourself — `apply` silently reverts your
scale:

```bash
kubectl apply -f examples/manifests/01-deployment.yaml && kubectl get deployment pingd -o jsonpath='replicas={.spec.replicas}{"\n"}'
```

Back to 3. Nothing warned you.

## Takeaways

- `kubectl apply` writes a record. Controllers do the work, asynchronously, by watching for
  differences.
- Five actors turn a Deployment into a running container: Deployment controller, ReplicaSet
  controller, scheduler, kubelet, and the status path back. They communicate only through
  the API server.
- Control loops are **level-triggered and continuous** — they observe current state rather
  than reacting to events, which is why they survive restarts and missed messages.
- You write `spec`; controllers write `status`. `generation` vs `observedGeneration` tells
  you whether your change has been seen; `conditions` tell you what a controller thinks.
- `ownerReferences` drive cascading deletion. You cannot delete the middle of the chain and
  have it stay deleted.
- Imperative commands write desired state with no record. `apply` will overwrite them
  silently. Manifests in git are the source of truth.

---

Previous: [Chapter 1 — Why Kubernetes](01-why-kubernetes.md) ·
Next: [Chapter 3 — Cluster anatomy](03-cluster-anatomy.md)

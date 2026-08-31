# Chapter 32 — Anti-patterns

The catalogue, each with the reason and the alternative. Most appear earlier in the book;
collecting them makes the review checklist.

Ordered roughly by how much damage they cause.

---

## Workloads

### No resource requests

**Why it's wrong.** The pod is `BestEffort` and **evicted first** under node pressure. It is
also invisible to the scheduler's accounting, so it contributes to overcommit without being
counted, and an HPA targeting CPU utilisation cannot work at all.

**Fix.** Set requests on everything; use a LimitRange to make it the namespace default.
*(Ch 8, 20, 28)*

---

### Liveness probe that checks dependencies

```yaml
livenessProbe:
  httpGet: { path: /health-including-database }   # ✗
```

**Why it's wrong.** A database blip fails liveness on every replica of every service at once,
Kubernetes restarts your entire fleet, and the reconnection storm delays the recovery that was
already happening.

**Fix.** Liveness checks only itself. Dependencies belong in **readiness**. *(Ch 9)*

---

### `localhost` in a probe

**Why it's wrong.** Inside a container `localhost` resolves to `::1`. An IPv4-only server
refuses the connection, the probe fails, and the container is restarted while working
perfectly.

**Fix.** `127.0.0.1`. *(Ch 9, 19)*

---

### No readiness probe

**Why it's wrong.** Pods receive traffic the moment the process starts, before they can serve.
Worse, **rollouts have nothing to gate on**, so a completely broken version rolls out to 100%.

**Fix.** A readiness probe is what makes rolling updates safe. *(Ch 6, 9)*

---

### `timeoutSeconds` left at the default

**Why it's wrong.** The default is **1 second**. A service that occasionally takes 1.2 s to
answer under load gets restarted, under load, making the load worse.

**Fix.** Set it deliberately, with `failureThreshold: 3` at minimum. *(Ch 9)*

---

### Bare pods

**Why it's wrong.** Nothing watches them. If it dies it stays dead; if its node dies it is
gone.

**Fix.** Deployment, StatefulSet, Job or DaemonSet. Bare pods are for debugging. *(Ch 5)*

---

### StatefulSet for a stateless service

**Why it's wrong.** Slower rollouts, slower scaling, and ordering guarantees you are paying for
and not using. A stuck pod blocks the entire rollout.

**Fix.** Deployment, unless you need stable identity, per-pod storage or ordered startup.
*(Ch 10)*

---

### A version label in the Deployment selector

```yaml
selector:
  matchLabels:
    app: pingd
    version: "1.4.2"    # ✗
```

**Why it's wrong.** `spec.selector` is **immutable**. Every release now requires deleting and
recreating the Deployment.

**Fix.** Keep the selector minimal and permanent; put changing labels in the pod template.
*(Ch 6)*

---

### Overlapping selectors between Deployments

**Why it's wrong.** Each ReplicaSet adopts any pod matching its selector, so two Deployments
continuously delete each other's pods. The symptom is endless pod churn with no obvious cause.

**Fix.** Selectors must be disjoint. *(Ch 6)*

---

### Jobs without `ttlSecondsAfterFinished`

**Why it's wrong.** Completed pods are retained forever, and clusters accumulate thousands.

**Fix.** Set it on every Job. Add `activeDeadlineSeconds` for anything that could hang.
*(Ch 10)*

---

### CronJob with default `concurrencyPolicy`

**Why it's wrong.** The default is `Allow`. A run that takes longer than the interval overlaps
with the next one — two copies of your nightly job writing to the same table.

**Fix.** `Forbid` or `Replace`, plus an explicit `timeZone`. *(Ch 10)*

---

## Configuration

### Config in env vars that you expect to update

**Why it's wrong.** **Environment variables freeze at container start.** Changing a ConfigMap
has no effect until the pod restarts — possibly days later, during an unrelated incident,
producing a version nobody deployed.

**Fix.** Mount as a volume (updates in ~1 min), or add a config checksum annotation to force a
rollout. `subPath` mounts **never** update. *(Ch 7)*

---

### Treating Secrets as secret

**Why it's wrong.** Base64 is an encoding. Anyone with `get secrets` in the namespace reads
them, and etcd stores them unencrypted by default.

**Fix.** Encryption at rest, tight RBAC (`list` on Secrets reads them all), and an external
secret store. *(Ch 7, 23)*

---

### Secrets in git

**Why it's wrong.** Permanent, and a `git revert` does not unpublish them.

**Fix.** Sealed Secrets, SOPS, or the External Secrets Operator. Rotate anything committed.
*(Ch 7, 27)*

---

## Networking

### Assuming namespaces isolate the network

**Why it's wrong.** **Every pod can reach every pod in every namespace by default.**

**Fix.** NetworkPolicy — in every namespace. *(Ch 14, 28)*

---

### NetworkPolicy on a CNI that ignores it

**Why it's wrong.** The objects apply cleanly and do nothing. You believe you are protected
and are not. Flannel does not enforce policy.

**Fix.** **Test enforcement** before relying on it. *(Ch 14)*

---

### Egress policy without DNS

**Why it's wrong.** Name resolution silently breaks for the selected pods, and nobody connects
the failure to the policy they just applied.

**Fix.** Always allow UDP **and** TCP port 53 to kube-dns. *(Ch 14)*

---

### NetworkPolicy using the Service port

**Why it's wrong.** Policy is evaluated after DNAT, so it sees the **pod** port. Writing the
Service port blocks everything.

**Fix.** Use the container's port. *(Ch 14)*

---

### An app bound to `127.0.0.1`

**Why it's wrong.** Reachable only inside its own pod. Probably the most common
containerisation bug there is — everything looks correct and nothing can reach it.

**Fix.** Bind `0.0.0.0`. *(Ch 19)*

---

### A LoadBalancer Service per microservice

**Why it's wrong.** Each one is a real cloud load balancer with a monthly bill and an IP.
Twenty services is twenty load balancers.

**Fix.** One Ingress or Gateway in front of many Services. *(Ch 11, 13)*

---

### Relying on ClusterIP to balance gRPC

**Why it's wrong.** Load balancing is **per connection**. HTTP/2 and gRPC use one long-lived
connection, which pins to a single pod forever.

**Fix.** Headless Service with client-side balancing, or a proxy that understands HTTP/2.
*(Ch 11)*

---

## Deployment

### `:latest`, or any mutable tag

**Why it's wrong.** You cannot tell what is running, cannot roll back, and replicas created at
different times run different code. On kind/minikube it also makes `imagePullPolicy` default
to `Always`, so your locally-loaded image is ignored.

**Fix.** Immutable tags, ideally digests, enforced at admission. *(Ch 17, 24; Docker book
Ch 15)*

---

### Default rollout strategy for a service under load

**Why it's wrong.** The default is `25%/25%`, which **dips below capacity on every rollout**.

**Fix.** `maxSurge: 1, maxUnavailable: 0`. *(Ch 6, 27)*

---

### `replicas` in a manifest managed by an HPA

**Why it's wrong.** Every `apply` or GitOps sync resets the count the HPA just calculated, and
the two fight — or you get a server-side apply conflict.

**Fix.** Remove the field. *(Ch 6, 25)*

---

### `kubectl edit` in production

**Why it's wrong.** Invisible to git, reverted by the next apply or by GitOps `selfHeal`, and
absent from any review or audit trail.

**Fix.** Change the manifest. Keep a documented break-glass procedure for genuine emergencies.
*(Ch 2, 27)*

---

### `kubectl rollout undo` as the rollback procedure

**Why it's wrong.** It desynchronises the cluster from git; the next sync re-applies the broken
version.

**Fix.** Emergency stop only, immediately followed by a git revert. *(Ch 6, 27)*

---

### Expecting `progressDeadlineSeconds` to roll back

**Why it's wrong.** It stops a stalled rollout. It does **not** reverse it.

**Fix.** Argo Rollouts or Flagger for automated rollback on metrics. *(Ch 6, 27)*

---

## Cluster

### No PodDisruptionBudget

**Why it's wrong.** A node drain can take every replica at once. This applies to
infrastructure too — CoreDNS without a PDB is a cluster-wide outage waiting for a node
upgrade.

**Fix.** `maxUnavailable: 1` on anything that matters. *(Ch 12, 25)*

---

### A PDB that can never be satisfied

```yaml
spec:
  minAvailable: 3        # with replicas: 3  ✗
```

**Why it's wrong.** Drains **block forever**, turning routine node maintenance into an
incident.

**Fix.** `maxUnavailable: 1`, which scales with the Deployment. Alert on
`disruptionsAllowed: 0`. *(Ch 25)*

---

### Expecting the scheduler to rebalance

**Why it's wrong.** It never does. After a drain, all your replicas can sit on one node
indefinitely — measured in Chapter 21.

**Fix.** `kubectl rollout restart` after node events; `topologySpreadConstraints` for
placement. *(Ch 3, 21, 25)*

---

### Webhook with `failurePolicy: Fail` and broad scope

**Why it's wrong.** When the webhook is down it blocks all pod creation cluster-wide —
including the pods that would restore it.

**Fix.** Exclude `kube-system`, short timeouts, narrow matching, run it HA. *(Ch 24)*

---

### Force-deleting a StatefulSet pod on an unreachable node

**Why it's wrong.** The container may still be running with the volume attached. Deleting the
API object lets a replacement start — two writers, one volume, corrupted data.

**Fix.** Confirm the node is truly gone first. *(Ch 17, 21)*

---

### Never upgrading

**Why it's wrong.** kubeadm certificates expire after **one year** and present as total
authentication failure. Support windows are months, not years, and skipping minor versions is
unsupported.

**Fix.** Regular upgrades; scan for removed APIs with `pluto`/`kubent` first. *(Ch 21)*

---

### No etcd backup

**Why it's wrong.** etcd is the only state. Losing it loses the cluster's definition of
itself.

**Fix.** Scheduled snapshots, **tested restores**, or a managed control plane. *(Ch 21)*

---

## Security

### Mounting the ServiceAccount token everywhere

**Why it's wrong.** Every pod carries an API credential it does not need, free for anyone who
achieves code execution.

**Fix.** `automountServiceAccountToken: false` unless the workload uses the API. The cheapest
security win available. *(Ch 23)*

---

### Every workload using the `default` ServiceAccount

**Why it's wrong.** No least privilege, and audit logs cannot attribute API calls to a
workload.

**Fix.** One ServiceAccount per workload. *(Ch 23)*

---

### Granting `cluster-admin` because something failed

**Why it's wrong.** It works, so it stays, forever.

**Fix.** `kubectl auth can-i` to find the missing permission and grant that. *(Ch 23)*

---

### Letting tenants create pods without Pod Security Standards

**Why it's wrong.** `create pods` plus `hostPath: /` is host root. **RBAC alone does not make
a tenant safe.**

**Fix.** PSS `baseline` minimum, `restricted` where possible. *(Ch 23, 28)*

---

### Signing images without verifying them

**Why it's wrong.** Ceremony. A signature nothing checks provides nothing.

**Fix.** Verify at admission. *(Ch 24)*

---

### Namespaces as a security boundary for untrusted tenants

**Why it's wrong.** Shared kernel, shared nodes, shared control plane, shared cluster-scoped
resources.

**Fix.** Separate clusters, vCluster, or sandboxed runtimes for anything untrusted. *(Ch 28)*

---

## Operations

### No metrics-server, then wondering why the HPA does nothing

**Why it's wrong.** The HPA reports `<unknown>` and never scales. It is not installed by
default.

**Fix.** Install it — separately from kube-prometheus-stack. *(Ch 25, 26)*

---

### Relying on `kubectl logs` after the fact

**Why it's wrong.** Pod logs are **deleted with the pod** — exactly when you want them.

**Fix.** Ship logs off-node. Export events too; they expire in about an hour. *(Ch 26)*

---

### Monitoring stack with no requests and no PDB

**Why it's wrong.** It is `BestEffort`, so it is evicted first — precisely when the cluster is
under pressure and you need it.

**Fix.** Requests and a PDB on your observability stack. *(Ch 20, 26)*

---

### Alerting on CPU utilisation

**Why it's wrong.** Noise. A pod at 90% CPU serving fine is not an incident, and the alert
that fires constantly is the alert nobody reads.

**Fix.** Alert on symptoms — error rate, latency, restarts, **CPU throttling ratio**, memory
against limit. *(Ch 26)*

---

### Building an operator instead of writing a Helm chart

**Why it's wrong.** You have added a privileged distributed system, a schema, a release
process and a failure mode, to save some YAML.

**Fix.** Operators are for encoded operational knowledge. Use packaging tools for packaging.
*(Ch 30)*

---

### Adopting Kubernetes with no present constraint

**Why it's wrong.** A large permanent operational burden to solve problems you do not have,
with engineering time going into the platform rather than the product.

**Fix.** Adopt for a specific constraint. A managed container service is often the better
answer. *(Ch 1)*

---

## The review checklist

For reviewing a workload manifest:

- [ ] Resource **requests** on every container; limits on memory
- [ ] Readiness probe, and a liveness probe that checks **only itself**
- [ ] Probes use `127.0.0.1`, a named port, and `timeoutSeconds` set deliberately
- [ ] `startupProbe` for slow starters
- [ ] Immutable image tag or digest; `imagePullPolicy` correct for your cluster
- [ ] `maxSurge: 1, maxUnavailable: 0`
- [ ] Minimal, permanent `selector` with no version label
- [ ] `securityContext`: non-root numeric UID, `allowPrivilegeEscalation: false`,
      `capabilities: drop: [ALL]`, `readOnlyRootFilesystem`, `seccompProfile: RuntimeDefault`
- [ ] Dedicated ServiceAccount, `automountServiceAccountToken: false` if unused
- [ ] `terminationGracePeriodSeconds` longer than the longest request; `preStop` sleep if
      rollouts drop connections
- [ ] PodDisruptionBudget with `maxUnavailable`
- [ ] `topologySpreadConstraints` across nodes
- [ ] Config from a mounted ConfigMap, or a checksum annotation to force rollouts
- [ ] No `replicas` if an HPA owns it
- [ ] Ownership labels for cost attribution
- [ ] NetworkPolicy exists for the namespace, DNS allowed in egress

---

Previous: [Chapter 31 — The ecosystem](31-ecosystem.md) ·
Next: [Appendix A — kubectl cheatsheet](../appendices/a-cheatsheet.md)

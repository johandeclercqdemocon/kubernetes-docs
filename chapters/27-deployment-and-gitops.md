# Chapter 27 — Deployment strategies and GitOps

Chapter 6 covered the mechanics of a rolling update. This chapter covers choosing a strategy,
and the question of what drives the cluster in the first place.

## Rolling update

The default, and the right answer for most services:

```yaml
spec:
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 1
      maxUnavailable: 0
```

The defaults are `25%/25%`, which **dips below capacity during every rollout**. Set
`maxUnavailable: 0` for anything serving traffic.

What makes it safe is readiness gating (Chapter 9): a new pod that never becomes ready stops
the rollout. Chapter 9 measured this — a broken readiness path left three old pods serving
while the new one sat `0/1`, and the rollout stalled rather than taking the service down.

The limitations are worth naming:

- **Two versions run simultaneously.** Your API and database schema must tolerate that.
- **No automatic rollback.** `progressDeadlineSeconds` stops a stalled rollout; it does not
  reverse it.
- **No traffic control.** You cannot send 5% of requests to the new version — the split is by
  replica count, and only in whole pods.

## Recreate

```yaml
spec:
  strategy:
    type: Recreate
```

Terminate everything, then start the new version. Downtime by design.

Legitimate when two versions genuinely cannot coexist: a singleton holding an exclusive lock, a
`ReadWriteOnce` volume, or a schema migration that is not backwards compatible. Choosing it
deliberately is fine; choosing it because it is simpler is not.

## Blue/green

Two full environments, traffic switched at once — implemented with plain Kubernetes by
changing a Service selector:

```yaml
spec:
  selector:
    app: pingd
    version: blue      # change to `green` to switch
```

Instant switch, instant rollback, no version mixing. Costs double the resources during the
transition, and the switch is atomic, so a bad version reaches 100% of traffic immediately —
you find out fast, which cuts both ways.

Note that in-flight connections to blue are not drained by a selector change; they simply stop
receiving *new* connections.

## Canary

Route a small fraction of traffic to the new version, watch, then proceed or abort.

**With replicas only** — crude but free:

```
9 pods labelled version=stable  +  1 pod labelled version=canary
```

Both matched by one Service, so roughly 10% of connections hit the canary. Granularity is
limited by replica count, and it is per *connection*, not per request (Chapter 11).

**With a Gateway API HTTPRoute** — proper weighting (Chapter 13):

```yaml
      backendRefs:
        - name: pingd-stable
          port: 80
          weight: 90
        - name: pingd-canary
          port: 80
          weight: 10
```

**With a progressive delivery controller** — Argo Rollouts or Flagger — the weights step
automatically while metrics are checked at each stage:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Rollout
spec:
  strategy:
    canary:
      steps:
        - setWeight: 10
        - pause: {duration: 5m}
        - analysis:
            templates:
              - templateName: success-rate
        - setWeight: 50
        - pause: {duration: 5m}
        - setWeight: 100
```

The analysis step is the point: it queries Prometheus for error rate or latency and **rolls
back automatically** if the canary is worse. This is the only approach in this chapter that
gives you automated rollback on real signal, and it is why progressive delivery is worth the
extra component for high-traffic services.

## Choosing

| Situation | Strategy |
|---|---|
| Ordinary stateless service | Rolling, `maxUnavailable: 0` |
| Versions cannot coexist | Recreate, accepting downtime |
| Need instant rollback, can afford 2× | Blue/green |
| High traffic, want automated safety | Canary with analysis |
| Database schema change | Expand/contract, independent of the above |

That last row matters more than the strategy choice. **Any strategy that runs two versions at
once requires backwards-compatible schema changes**: add columns, deploy code that writes both,
migrate, then remove — across several releases. Getting this wrong makes every rollout
strategy equally broken, and it is where most deployment incidents actually originate.

## GitOps

The deployment question underneath all of the above: what puts manifests into the cluster?

**Push** — CI runs `kubectl apply`. Simple, and it means your CI system holds cluster
credentials, and nothing detects or corrects drift.

**Pull (GitOps)** — a controller *in* the cluster watches a git repository and reconciles.
Credentials never leave the cluster, drift is corrected continuously, and git is the audit
log.

The second is Chapter 2's model applied to deployment: git holds desired state, a controller
converges towards it, continuously.

**Argo CD** — a UI-forward implementation with strong visualisation of sync status and diffs:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: pingd
  namespace: argocd
spec:
  project: default
  source:
    repoURL: https://github.com/org/manifests
    targetRevision: main
    path: apps/pingd/overlays/production
  destination:
    server: https://kubernetes.default.svc
    namespace: production
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
```

`selfHeal: true` reverts manual changes; `prune: true` deletes resources removed from git.
Both are the point of GitOps and both will surprise someone the first time.

**Flux** — a set of controllers, no UI, more composable, integrates naturally with Helm and
Kustomize.

### What GitOps actually gives you

- **The cluster's state is reviewable** — every change is a pull request.
- **Drift is corrected**, so `kubectl edit` in production stops being a permanent invisible
  change (Chapter 2's imperative/declarative conflict, solved).
- **Rollback is `git revert`.** This resolves the tension in Chapter 6: `kubectl rollout undo`
  desynchronises the cluster from git, whereas a revert fixes both.
- **CI does not need cluster credentials** — it builds and pushes images, and updates a tag in
  git.

### What it does not solve

- **Secrets.** Plain Secrets cannot go in git. Use Sealed Secrets, SOPS, or the External
  Secrets Operator (Chapter 7).
- **The image tag update.** Something must write the new tag into git — a CI step, Argo CD
  Image Updater, or Flux's image automation. This is the most commonly under-designed part of
  a GitOps setup.
- **Ordering across applications.** Sync waves and health checks help; complex inter-app
  dependencies are still awkward.
- **Emergency changes.** `selfHeal` will revert your 3am `kubectl edit` within minutes. Have a
  documented break-glass procedure — usually pausing sync — and expect to need it.

## Repository layout

The pattern that scales:

```
manifests/
  base/
    pingd/                    # kustomize base
  overlays/
    staging/
      kustomization.yaml      # patches: replicas, image tag, resources
    production/
      kustomization.yaml
```

Keep **application code and manifests in separate repositories** once more than one team is
involved. A single repo is simpler at first and becomes painful: every image tag update
creates a commit in the application repo, triggering CI, which builds an image, which updates
a tag. Separating them breaks that loop.

Chapter 29 covers Kustomize and Helm in detail.

## Deploying the running example

Whatever drives it, the pipeline shape from the Docker book holds:

```
build image → test → scan → push with an immutable tag
  → update the tag in the manifests repo
  → GitOps controller syncs
  → wait for rollout, verify health
```

```bash
kubectl rollout status deployment/pingd --timeout=300s
```

That command belongs in any push-based pipeline — it blocks until ready and exits non-zero on
failure, which is what turns "the API accepted my manifest" into "the new version is actually
serving".

## Try it

Watch a rolling update maintain capacity, using the running example:

```bash
kubectl set image deployment/pingd api=pingd:latest --record=false && kubectl set env deployment/pingd PINGD_VERSION=2.0.0
```

```bash
for i in $(seq 1 10); do kubectl get deployment pingd -o jsonpath='ready={.status.readyReplicas} updated={.status.updatedReplicas} total={.status.replicas}{"\n"}'; sleep 2; done
```

`ready` never drops below 3 because `maxUnavailable: 0`.

Now simulate blue/green with a Service selector. Create a second Deployment labelled `green`:

```bash
kubectl apply -f examples/manifests/27-bluegreen.yaml && kubectl rollout status deployment/pingd-green --timeout=180s
```

Note the two sets of pod IPs:

```bash
kubectl get pods -l app=pingd -o jsonpath='blue:  {.items[*].status.podIP}{"\n"}'; kubectl get pods -l app=pingd-green -o jsonpath='green: {.items[*].status.podIP}{"\n"}'
```

```
blue:  10.244.2.27 10.244.2.29 10.244.2.26
green: 10.244.1.47 10.244.2.32 10.244.1.48
```

Point the Service at green and watch the endpoints move wholesale:

```bash
kubectl patch service pingd -p '{"spec":{"selector":{"app":"pingd-green"}}}'
```

```bash
sleep 6 && kubectl get endpointslice -l kubernetes.io/service-name=pingd -o jsonpath='{.items[*].endpoints[*].addresses[0]}{"\n"}'
```

```
10.244.1.48 10.244.1.47 10.244.2.32
```

Entirely green IPs, in one atomic change. Switch back instantly — this is the blue/green
rollback:

```bash
kubectl patch service pingd -p '{"spec":{"selector":{"app":"pingd"}}}'
```

Clean up:

```bash
kubectl delete -f examples/manifests/27-bluegreen.yaml && kubectl rollout undo deployment/pingd
```

## Takeaways

- Rolling with `maxUnavailable: 0` is the default answer. Readiness gating is what makes it
  safe; without a readiness probe a broken version rolls out completely.
- Rolling updates **do not roll back automatically**. `progressDeadlineSeconds` only stops.
- Blue/green gives instant switch and rollback at double the cost; canary gives graduated
  exposure but needs a router that can weight traffic.
- **Only progressive delivery with metric analysis (Argo Rollouts, Flagger) gives automated
  rollback on real signal.**
- Any strategy running two versions at once requires **backwards-compatible schema changes**.
  This causes more incidents than the strategy choice does.
- GitOps is Chapter 2's reconciliation applied to deployment: git is desired state, drift is
  corrected, rollback is `git revert`, and CI never holds cluster credentials.
- GitOps does not solve secrets, the image-tag update loop, cross-app ordering, or emergency
  changes — `selfHeal` will revert your manual fix.
- Separate application and manifest repositories once more than one team is involved.

---

Previous: [Chapter 26 — Observability](26-observability.md) ·
Next: [Chapter 28 — Multi-tenancy](28-multi-tenancy.md)

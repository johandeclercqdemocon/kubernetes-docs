# Chapter 7 — Configuration and secrets

Build once, configure per environment. That principle carries over from containers
unchanged; what changes is that Kubernetes gives you first-class objects for it, with
behaviours that are not obvious and cause real outages.

## ConfigMaps

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: pingd-config
data:
  LOG_LEVEL: "INFO"
  PINGD_GREETING: "pong"
  app.yaml: |
    feature_flags:
      new_router: false
    timeouts:
      upstream_seconds: 5
```

Two shapes in one object: simple key-value pairs, and whole files as multi-line values.
Which you use determines how you consume it.

### Consuming as environment variables

```yaml
containers:
  - name: api
    envFrom:
      - configMapRef:
          name: pingd-config
    env:
      - name: LOG_LEVEL
        valueFrom:
          configMapKeyRef:
            name: pingd-config
            key: LOG_LEVEL
```

`envFrom` injects every key; `configMapKeyRef` picks one and can rename it. Note that
`envFrom` will silently skip keys that are not valid environment variable names — so
`app.yaml` above does not become an env var, and nothing tells you.

### Consuming as files

```yaml
    volumeMounts:
      - name: config
        mountPath: /etc/pingd
        readOnly: true
volumes:
  - name: config
    configMap:
      name: pingd-config
      items:
        - key: app.yaml
          path: app.yaml
```

Without `items`, every key becomes a file in that directory. With `items`, you choose.

**Mounting a ConfigMap over a directory hides the image's contents at that path**, exactly
like a bind mount in Docker. To add a file to an existing directory, use `subPath` — but
read the next section first, because `subPath` has a serious catch.

## The update behaviour that catches everyone

This is the most important thing in the chapter.

**Environment variables are frozen when the container starts. Volume-mounted files update
in place.**

Measured. A pod reading the same ConfigMap key both ways, before and after the ConfigMap was
changed:

```
before change: env=hello file=hello
after change:  env=hello file=CHANGED
```

The file updated; the environment variable did not, and never will for the life of that
container. The kubelet refreshes mounted ConfigMap and Secret volumes periodically (the sync
period plus cache TTL — roughly a minute in practice; the change above appeared within 75
seconds).

The consequences:

- **Changing a ConfigMap does not restart anything.** No rollout, no notification. If your
  app reads config via env vars, the change has no effect until the pods restart for some
  unrelated reason — possibly days later, possibly during an incident, producing a version
  of your service nobody deployed.
- **`subPath` mounts do not update at all.** They are resolved once. This is the catch:
  `subPath` is the natural way to place a single config file into an existing directory, and
  it silently opts you out of updates.

Three ways to handle it:

**1. Restart deliberately after a change.**

```bash
kubectl rollout restart deployment/pingd
```

**2. Make the change force a rollout**, by putting a hash of the config into the pod
template annotation. Helm does this idiomatically:

```yaml
spec:
  template:
    metadata:
      annotations:
        checksum/config: {{ include (print $.Template.BasePath "/configmap.yaml") . | sha256sum }}
```

Changing the ConfigMap changes the annotation, which changes the template hash, which
triggers a normal rolling update. This is the best answer for most cases.

**3. Use immutable ConfigMaps and version their names** — `pingd-config-v2` — so a config
change is necessarily a Deployment change:

```yaml
immutable: true
```

Immutable ConfigMaps also reduce API server load significantly at scale, because the kubelet
stops watching them.

## Secrets

Structurally almost identical to ConfigMaps, with base64-encoded values:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: pingd-secret
type: Opaque
stringData:
  DATABASE_PASSWORD: "not-a-real-password"
```

`stringData` takes plaintext and encodes it for you on write; `data` requires you to encode
it yourself. Always use `stringData` in manifests you write by hand.

### Secrets are not secret

The single most important fact, and it surprises people who assume the name means something:

```bash
kubectl get secret demo-secret -o jsonpath='{.data.DB_PASSWORD}'
```

```
aHVudGVyMg==
```

```bash
kubectl get secret demo-secret -o jsonpath='{.data.DB_PASSWORD}' | base64 -d
```

```
hunter2
```

**Base64 is an encoding, not encryption.** Anyone with `get secrets` in the namespace reads
every secret in it. And by default, etcd stores them the same way — unencrypted at rest —
so anyone with an etcd backup has them all.

What actually protects Secrets:

**Encryption at rest.** Configure `EncryptionConfiguration` on the API server so Secrets are
encrypted in etcd, ideally with a KMS provider so the key is not on the node. Managed
clusters usually offer this as a checkbox; verify it is on rather than assuming.

**RBAC.** Very few subjects should have `get`/`list` on Secrets. Note that `list` on Secrets
in a namespace is equivalent to reading all of them.

**An external secret store.** Vault, AWS Secrets Manager, GCP Secret Manager. Either the
External Secrets Operator syncs into Kubernetes Secrets, or the Secrets Store CSI driver
mounts them directly without creating a Secret object at all. The latter is stronger.

**Do not commit Secrets to git.** For GitOps, use Sealed Secrets (encrypted with a
cluster-held key, safe to commit) or SOPS, or keep secrets entirely outside git with the
External Secrets Operator.

### Prefer files over environment variables

The Docker book's argument applies here with more force, because Kubernetes makes the
leakage easier:

```bash
kubectl describe pod POD          # env var *names* and configMapRef sources
kubectl get pod POD -o yaml       # inline env values, in full
```

Environment variables are also inherited by child processes and captured by crash handlers.
Mounted secret files are read only by code that opens them, and can be `defaultMode: 0400`.

```yaml
volumes:
  - name: secret
    secret:
      secretName: pingd-secret
      defaultMode: 0400
```

Secret volumes are mounted as **tmpfs**, so they never touch the node's disk.

## The downward API

Exposes pod metadata to the container — useful for logging, metrics labelling and
sharding:

```yaml
env:
  - name: POD_NAME
    valueFrom:
      fieldRef:
        fieldPath: metadata.name
  - name: NODE_NAME
    valueFrom:
      fieldRef:
        fieldPath: spec.nodeName
  - name: POD_IP
    valueFrom:
      fieldRef:
        fieldPath: status.podIP
  - name: MEMORY_LIMIT
    valueFrom:
      resourceFieldRef:
        containerName: api
        resource: limits.memory
```

That last one is genuinely valuable: it lets a runtime size itself from its actual limit
rather than from the node's total memory. It is the Kubernetes-native fix for the problem
Chapter 8 describes.

Confirm what a pod received:

```bash
kubectl exec deploy/pingd -- env | grep -E 'POD_|NODE_'
```

## Ordering and precedence

Later `env` entries win over `envFrom`, so you can set defaults in bulk and override
individually:

```yaml
envFrom:
  - configMapRef:
      name: pingd-config      # LOG_LEVEL=INFO
env:
  - name: LOG_LEVEL
    value: "DEBUG"            # wins
```

Variables can reference earlier ones with `$(VAR)`, but only ones defined earlier in the
same list — not shell expansion, and not arbitrary ordering:

```yaml
env:
  - name: HOST
    value: "db"
  - name: DATABASE_URL
    value: "postgres://$(HOST):5432/pingd"
```

## Missing references

A missing ConfigMap or Secret key does not fail at apply time. The Deployment is accepted,
the pod is created, and it sits in `CreateContainerConfigError`:

```bash
kubectl get pods
```

```
NAME          READY   STATUS                       RESTARTS   AGE
pingd-xxxxx   0/1     CreateContainerConfigError   0          10s
```

```bash
kubectl describe pod POD | grep -A3 Events
```

```
Error: configmap "pingd-config" not found
```

Chapter 17 covers this state. Make it optional if it genuinely is:

```yaml
envFrom:
  - configMapRef:
      name: optional-config
      optional: true
```

## Try it

Prove Secrets are just encoded:

```bash
kubectl create secret generic demo-secret --from-literal=DB_PASSWORD='hunter2'
```

```bash
kubectl get secret demo-secret -o jsonpath='{.data.DB_PASSWORD}' | base64 -d; echo
```

Now the update-behaviour experiment, which is the one worth running yourself. Create a
ConfigMap and a pod that reads the same key both ways:

```bash
kubectl create configmap demo-cm --from-literal=GREETING=hello
```

```bash
kubectl apply -f examples/manifests/07-configmap-update-demo.yaml
```

```bash
kubectl wait --for=condition=Ready pod/cmtest --timeout=90s && kubectl logs cmtest --tail=1
```

```
env=hello file=hello
```

Change the ConfigMap:

```bash
kubectl create configmap demo-cm --from-literal=GREETING=CHANGED --dry-run=client -o yaml | kubectl apply -f -
```

Wait about 75 seconds for the kubelet to resync the volume, then look again:

```bash
sleep 75 && kubectl logs cmtest --tail=1
```

```
env=hello file=CHANGED
```

The file changed. The environment variable did not, and will not until the container
restarts. Clean up:

```bash
kubectl delete pod cmtest --force --grace-period=0 2>/dev/null; kubectl delete cm demo-cm secret demo-secret
```

Finally, see the downward API in the running example:

```bash
kubectl exec deploy/pingd -- env | grep -E 'POD_NAME|NODE_NAME'
```

## Takeaways

- ConfigMaps and Secrets are consumed as env vars or as files, and the choice determines
  update behaviour.
- **Env vars freeze at container start; volume-mounted files update in place** (~1 minute).
  Measured: `env=hello file=CHANGED` after a config change.
- **`subPath` mounts never update.** It is the natural way to place one file, and it silently
  opts out of refresh.
- Changing a ConfigMap restarts nothing. Use a config checksum annotation to force a rollout,
  or version the ConfigMap name and mark it immutable.
- **Secrets are base64, not encrypted**, and unencrypted in etcd by default. Enable
  encryption at rest, restrict RBAC (`list` on Secrets reads them all), and prefer an
  external store.
- Prefer secret **files** (tmpfs, mode 0400) over env vars.
- The downward API can pass the container's real memory limit into the runtime — the fix for
  Chapter 8's sizing problem.
- Missing ConfigMap/Secret references fail at container creation, not at apply.

---

Previous: [Chapter 6 — Deployments and ReplicaSets](06-deployments.md) ·
Next: [Chapter 8 — Resources, requests and QoS](08-resources-and-qos.md)

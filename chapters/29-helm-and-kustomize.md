# Chapter 29 — Packaging: Helm and Kustomize

Raw manifests stop scaling the moment you need the same application in staging and production
with different replica counts, image tags and resource limits. Copying YAML and editing three
values is how drift starts.

Two tools solve this differently, and the difference is worth understanding before choosing.

## Kustomize — overlays, no templating

Built into `kubectl`. You write plain, valid YAML and describe **patches** on top of it.

```
manifests/
  base/
    kustomization.yaml
    deployment.yaml
    service.yaml
  overlays/
    staging/kustomization.yaml
    production/kustomization.yaml
```

```yaml
# base/kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - deployment.yaml
  - service.yaml
commonLabels:
  app.kubernetes.io/name: pingd
```

```yaml
# overlays/production/kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: production
resources:
  - ../../base
replicas:
  - name: pingd
    count: 10
images:
  - name: pingd
    newName: ghcr.io/org/pingd
    newTag: 1.4.2
patches:
  - target:
      kind: Deployment
      name: pingd
    patch: |-
      - op: replace
        path: /spec/template/spec/containers/0/resources/limits/memory
        value: 1Gi
```

```bash
kubectl apply -k overlays/production
```

```bash
kubectl kustomize overlays/production          # render without applying
```

**The strength**: the base is a real, valid manifest. You can `kubectl apply -f
base/deployment.yaml` and it works. Your editor validates it, `kubectl explain` applies, and
diffs are readable.

**The weakness**: only what Kustomize can express. Conditionals, loops and computed values are
absent by design. "Deploy an Ingress only in production" means a separate resource in the
production overlay, not an `{{ if }}`.

Two features worth knowing:

**`configMapGenerator`** appends a content hash to the ConfigMap name, so changing config
changes the name, which changes the pod template, which **triggers a rollout automatically** —
solving Chapter 7's "config changed but nothing restarted" problem cleanly:

```yaml
configMapGenerator:
  - name: pingd-config
    literals:
      - LOG_LEVEL=INFO
```

**`components`** are reusable, optional overlay fragments — the closest Kustomize gets to
conditionals.

## Helm — templating and releases

```
pingd/
  Chart.yaml
  values.yaml
  templates/
    deployment.yaml
    service.yaml
    _helpers.tpl
```

```yaml
# templates/deployment.yaml
spec:
  replicas: {{ .Values.replicaCount }}
  template:
    metadata:
      annotations:
        checksum/config: {{ include (print $.Template.BasePath "/configmap.yaml") . | sha256sum }}
    spec:
      containers:
        - name: api
          image: "{{ .Values.image.repository }}:{{ .Values.image.tag | default .Chart.AppVersion }}"
          resources:
            {{- toYaml .Values.resources | nindent 12 }}
{{- if .Values.ingress.enabled }}
# ... an Ingress, only when enabled
{{- end }}
```

```bash
helm install pingd ./pingd -f values-production.yaml
helm upgrade pingd ./pingd --set image.tag=1.4.3
helm rollback pingd 3
helm template pingd ./pingd -f values-production.yaml    # render locally
helm diff upgrade pingd ./pingd                          # plugin, very worth installing
```

**The strengths**: real conditionals and loops; a **package** others can install with one
command; release history and `helm rollback`; and a huge ecosystem — most third-party
software ships a chart.

**The weaknesses**, stated plainly:

- **Templates are not YAML.** They are Go text templates that produce YAML, so indentation
  bugs are common, editors cannot validate them, and errors surface as unhelpful parse
  failures. `nindent` exists because of this.
- **Values files become sprawling.** A mature chart has hundreds of options, most unused, and
  understanding one requires reading the templates.
- **Debugging is indirect.** `helm template` is essential; without it you are guessing.
- **Release state lives in cluster Secrets**, which can drift from git and complicates
  GitOps.

## Choosing

**Kustomize** for your own applications, especially with GitOps. The output is auditable,
diffs are meaningful, and you never debug a template.

**Helm** for distributing software to others, and for consuming third-party software — which
is most of the ecosystem.

**Both together** is common and works well: consume third-party charts with Helm, manage your
own manifests with Kustomize. Both Argo CD and Flux support rendering a Helm chart and then
applying Kustomize patches on top, which lets you fix a chart's shortcomings without forking
it.

A pragmatic middle path some teams take: use `helm template` to render third-party charts to
plain YAML, commit that, and manage everything with Kustomize. You lose `helm upgrade` and
gain a fully auditable, diffable repository.

## Practices that avoid pain

**Pin chart versions.** `helm install foo/bar` without `--version` takes whatever is current —
the same mutable-tag problem as `:latest`.

```bash
helm install monitoring prometheus-community/kube-prometheus-stack --version 66.2.1
```

**Render and review before upgrading.** `helm diff upgrade` shows what will change; a chart
version bump can rewrite far more than you expect, including CRDs.

**Never `kubectl edit` a Helm-managed resource.** The next `helm upgrade` reverts it, and your
change is invisible in the meantime. Same principle as Chapter 2's imperative/declarative
conflict.

**CRDs are a Helm weak spot.** `helm install` creates CRDs from `crds/`, but `helm upgrade`
does **not** update them, and `helm uninstall` does not remove them. CRD upgrades are a
manual step, and getting this wrong breaks operators in confusing ways. Read the chart's
upgrade notes.

**Keep values files small and per-environment.** A single values file with every option set
is unreadable; rely on chart defaults and override only what differs.

**Do not template what you can patch.** If a value differs in one environment, a Kustomize
patch is clearer than a new chart parameter that must be threaded through templates.

## The image tag question

Both tools face Chapter 27's unsolved problem: **something must update the image tag** for a
new build.

- **CI writes to the manifests repo** — explicit, auditable, and the commit is the deployment
  record. Most common with GitOps.
- **Argo CD Image Updater / Flux image automation** — a controller watches the registry and
  commits the update. Less pipeline code, more moving parts.
- **`helm upgrade --set image.tag=...` from CI** — simple, and it puts cluster credentials in
  CI and leaves git out of date.

The first is the one to prefer if you have GitOps at all.

## Try it

Kustomize needs nothing installed. Build a base and two overlays:

```bash
mkdir -p /tmp/kdemo/base /tmp/kdemo/overlays/production
```

```bash
cp examples/manifests/01-deployment.yaml examples/manifests/02-service.yaml /tmp/kdemo/base/
```

```bash
cat > /tmp/kdemo/base/kustomization.yaml <<'EOF'
resources:
  - 01-deployment.yaml
  - 02-service.yaml
EOF
```

```bash
cat > /tmp/kdemo/overlays/production/kustomization.yaml <<'EOF'
resources:
  - ../../base
namePrefix: prod-
replicas:
  - name: pingd
    count: 5
images:
  - name: pingd
    newTag: "1.4.2"
EOF
```

Render it without applying anything:

```bash
kubectl kustomize /tmp/kdemo/overlays/production | grep -E 'name:|replicas:|image:' | head -12
```

Five replicas, a `prod-` prefix and a pinned tag, from a base that was never modified.

Compare against the base:

```bash
diff <(kubectl kustomize /tmp/kdemo/base) <(kubectl kustomize /tmp/kdemo/overlays/production) | head -20
```

That diff is the whole argument for overlays: you can see precisely what an environment
changes.

Now see what a Helm chart renders to, without installing it:

```bash
helm template demo oci://registry-1.docker.io/bitnamicharts/nginx --version 18.2.5 2>/dev/null | head -30 || echo "(helm not installed — skip)"
```

Clean up:

```bash
rm -rf /tmp/kdemo
```

## Takeaways

- **Kustomize patches valid YAML; Helm templates text.** That difference determines
  everything else about how each fails.
- Kustomize's base is a real manifest you can apply and validate. Its limits are deliberate —
  no conditionals or loops.
- `configMapGenerator` hashes content into the name, which **triggers a rollout on config
  change** — the clean fix for Chapter 7's problem.
- Helm gives conditionals, packaging, release history and the ecosystem. It costs you
  non-YAML templates, sprawling values files, and indirect debugging.
- Use **Kustomize for your own apps, Helm for third-party software**. Combining them is normal.
- **Pin chart versions**; `helm diff upgrade` before upgrading.
- **Helm does not upgrade CRDs.** That is a manual step and a common source of broken
  operators.
- Never `kubectl edit` a Helm- or Kustomize-managed resource; the next apply reverts it.
- Something still has to update the image tag — prefer CI committing to the manifests repo.

---

Previous: [Chapter 28 — Multi-tenancy](28-multi-tenancy.md) ·
Next: [Chapter 30 — Operators and CRDs](30-operators-and-crds.md)

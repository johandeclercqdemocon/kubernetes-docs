# Chapter 18 — Getting inside

The Docker book had a chapter on this because production images have no shell. Kubernetes has
the same problem and a better answer: **ephemeral containers**, which let you attach a
debugging toolbox to a running pod without modifying it, restarting it, or shipping tools in
your image.

## `kubectl exec`, and where it stops

```bash
kubectl exec -it POD -- sh
```

```bash
kubectl exec -it POD -c CONTAINER -- sh          # multi-container pod
kubectl exec POD -- env | sort
kubectl exec POD -- cat /etc/resolv.conf
```

Same constraint as Docker: it runs a binary **that must exist in the image**. On a distroless
pod:

```bash
kubectl exec distro -- sh -c 'echo hi'
```

```
error: Internal error occurred: ... OCI runtime exec failed:
exec: "sh": executable file not found in $PATH
```

That is the image doing its job. You need a different tool.

## Ephemeral containers

`kubectl debug` injects a container into a **running** pod, sharing its namespaces:

```bash
kubectl debug POD --image=nicolaka/netshoot --target=CONTAINER -it -- bash
```

Measured against the distroless pod above:

```bash
kubectl debug distro --image=busybox:1.37 --target=distro -it -- sh
```

```
# ps -o pid,comm
PID   COMMAND
    1 python3          ← the target's process
   19 sh               ← us
```

```
# ls /proc/1/root/
bin  boot  dev  etc  home  lib ...
```

Two things worked there that are worth naming.

**`--target` shares the process namespace**, so `ps` shows the target's processes and PID 1 is
the application. Without `--target` you get a container in the pod's network and IPC
namespaces but its own PID namespace — still useful for networking, useless for inspecting
processes.

**`/proc/1/root/` is the target container's filesystem.** Same trick as the Docker book: the
debug container has BusyBox's filesystem at `/`, and reaches the application's through
`/proc`. So you can read its config files, check whether a path exists, and inspect what it
actually shipped — with no shell in the target image.

The rest of `/proc` is equally available:

```bash
cat /proc/1/environ | tr '\0' '\n'     # the target's environment
cat /proc/1/cmdline | tr '\0' ' '      # its exact command line
ls -l /proc/1/fd/                      # open files and sockets
cat /proc/1/limits                     # ulimits in force
```

### The caveats

**Ephemeral containers cannot be removed.** They are recorded on the pod permanently:

```bash
kubectl get pod distro -o jsonpath='{.spec.ephemeralContainers[*].name}'
```

```
debugger-dpl9x debugger-6c8qt
```

Two debug sessions, two containers, both there forever. They stop running but stay in the
spec, and the only way to clear them is to delete the pod. Debug a few times on the same pod
and the spec accumulates clutter — mildly annoying, and worth knowing before you wonder why
the pod looks strange afterwards.

**They have no resource requests**, so they consume from the node without being accounted for.
Fine occasionally; do not leave them running.

**They are subject to the same policy as any container.** Pod Security Standards (Chapter 23)
apply, and a `restricted` namespace will reject a debug container asking for privileges. This
is correct and occasionally inconvenient.

**RBAC**: creating one needs `pods/ephemeralcontainers` `patch`, which is separate from
`pods/exec`. Granting `exec` does not grant `debug`.

## `kubectl debug` in its three modes

**1. Ephemeral container in an existing pod** — the default, above. Non-disruptive, and what
you want for a *running* pod.

**2. Copy the pod with changes** — for pods that are crashing, where there is nothing to
attach to:

```bash
kubectl debug POD --copy-to=POD-debug --container=api -- sleep 3600
```

```bash
kubectl debug POD --copy-to=POD-debug --set-image=api=busybox:1.37 -it -- sh
```

This creates a **new** pod with the same volumes, environment and configuration but a
different command or image. It is the cleanest answer to a `CrashLoopBackOff` where the
container dies before you can look at anything (Chapter 17). Add `--share-processes` if you
need a shared PID namespace in the copy.

Remember to delete the copy afterwards — it is a real pod consuming real resources.

**3. A node debug pod** — for the node itself:

```bash
kubectl debug node/k8sbook-worker -it --image=nicolaka/netshoot
```

This creates a privileged pod on that node with the **host filesystem mounted at `/host`** and
host namespaces shared. From there:

```bash
chroot /host
journalctl -u kubelet --since '10 min ago'
crictl ps
df -h
dmesg -T | tail
```

This is how you investigate a node without SSH, and it is the technique Chapter 21 relies on.
It also, obviously, grants host root — so it is a privileged operation that should be tightly
controlled by RBAC.

## Other ways in

**`kubectl cp`** — works both directions, and on any pod with `tar` present:

```bash
kubectl cp POD:/app/config.yaml ./config.yaml
kubectl cp ./fix.conf POD:/tmp/fix.conf
kubectl cp -c CONTAINER POD:/path ./local
```

The `tar` requirement is real: on a distroless image `kubectl cp` fails, and you fall back to
an ephemeral container reading `/proc/1/root/`.

**`kubectl port-forward`** — a tunnel straight to a pod or Service, bypassing Ingress,
Service routing and DNS:

```bash
kubectl port-forward pod/POD 8080:8000
kubectl port-forward deploy/pingd 8080:8000
kubectl port-forward svc/pingd 8080:80
```

Invaluable for localising network problems (Chapter 19): if `port-forward` works and the
Service does not, the application is fine and the problem is in Service routing.

Note that `port-forward svc/...` still forwards to **one pod**, not through the Service's load
balancing — it resolves the Service to a pod and connects there.

**`kubectl attach`** — connects to PID 1's existing stdio rather than starting a new process.
Rarely what you want, but occasionally the only way to interact with an interactive process.

**`kubectl proxy`** — an authenticated local proxy to the API server, for exploring the API
directly:

```bash
kubectl proxy --port=8001 &
curl -s localhost:8001/api/v1/namespaces/default/pods | head
```

## A note on `kubectl exec` and security

`pods/exec` is a **privileged permission**. It bypasses everything: the container's identity,
your audit trail of what the application does, and any application-level authorisation. A user
with `exec` on a pod can read its Secrets from the environment, its service account token from
`/var/run/secrets/...`, and use that token to act as the pod.

Treat `exec` and `debug` as production-privileged operations in your RBAC design
(Chapter 23), and make sure API server audit logging captures them.

## Try it

Create a pod with no shell:

```bash
kubectl run distro --image=gcr.io/distroless/python3-debian12:nonroot --restart=Never --command -- python3 -c "import http.server,socketserver;socketserver.TCPServer(('',8080),http.server.SimpleHTTPRequestHandler).serve_forever()"
```

```bash
kubectl wait --for=condition=Ready pod/distro --timeout=120s
```

Confirm `exec` cannot help:

```bash
kubectl exec distro -- sh -c 'echo hi'
```

Now attach a toolbox sharing its process namespace:

```bash
kubectl debug distro --image=busybox:1.37 --target=distro -it -- sh
```

Inside, look at the target's processes and filesystem:

```bash
ps -o pid,comm
ls /proc/1/root/
cat /proc/1/cmdline | tr '\0' ' '; echo
```

Exit, then see that the ephemeral container is now part of the pod forever:

```bash
kubectl get pod distro -o jsonpath='{.spec.ephemeralContainers[*].name}{"\n"}'
```

Try the copy mode, which is what you use for a crash-looping pod:

```bash
kubectl debug distro --copy-to=distro-probe --container=distro -- sleep 3600
```

```bash
sleep 10 && kubectl get pod distro-probe
```

And a node debug pod — this gives you the host filesystem:

```bash
kubectl debug node/k8sbook-worker -it --image=busybox:1.37 -- sh -c 'ls /host; echo ---; cat /host/etc/hostname'
```

Clean up. **⚠️ the node debugger pod is privileged — remove it:**

```bash
kubectl delete pod distro distro-probe --force --grace-period=0
```

```bash
kubectl get pods --field-selector spec.nodeName=k8sbook-worker | grep node-debugger
```

```bash
kubectl delete pod -l '!app' --field-selector status.phase=Running --force --grace-period=0 2>/dev/null | grep node-debugger
```

## Takeaways

- `kubectl exec` needs the binary to exist in the image. Distroless images have none, by
  design.
- **`kubectl debug --target` attaches an ephemeral container sharing the target's process
  namespace** — `ps` shows the application, and `/proc/1/root/` is its filesystem.
- Without `--target`, you share network and IPC but not PID.
- **Ephemeral containers cannot be removed** and accumulate in the pod spec. They have no
  resource requests.
- `kubectl debug --copy-to` makes a modified copy of a pod — the answer for crash loops where
  there is nothing to attach to.
- `kubectl debug node/NAME` gives a privileged pod with the host at `/host`. That is host
  root; control it with RBAC.
- `kubectl cp` needs `tar` in the image; `port-forward` bypasses Services and DNS, which makes
  it a localisation tool.
- `pods/exec` and `pods/ephemeralcontainers` are privileged permissions and are separate in
  RBAC.

---

Previous: [Chapter 17 — Pods that won't run](17-pods-wont-run.md) ·
Next: [Chapter 19 — Network diagnosis](19-debugging-networks.md)

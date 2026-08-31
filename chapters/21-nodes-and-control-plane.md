# Chapter 21 — Nodes and the control plane

Layer 5. When the platform itself is the problem, application-level debugging goes in circles
— every service looks broken and none of them is at fault.

## Node conditions

```bash
kubectl get nodes
```

```bash
kubectl get node NODE -o jsonpath='{range .status.conditions[*]}{.type}={.status} ({.reason}){"\n"}{end}'
```

```
MemoryPressure=False (KubeletHasSufficientMemory)
DiskPressure=False (KubeletHasNoDiskPressure)
PIDPressure=False (KubeletHasSufficientPID)
Ready=True (KubeletReady)
```

`Ready=True` means the kubelet has reported in recently *and* considers itself healthy. The
three pressure conditions are Chapter 20's subject.

### `NotReady`

The node stopped heartbeating, or the kubelet reported itself unhealthy. What follows is a
timeline worth knowing precisely, because it determines how long you have:

```
t+0s     kubelet stops reporting
t+~40s   node-monitor-grace-period expires → node marked NotReady
         → node controller adds a NoExecute taint
t+~5m    tolerationSeconds expires → pods marked for deletion, rescheduled elsewhere
```

**Between t+40s and t+5m, the containers are still running on the unreachable node.** Nothing
stopped them. The kubelet is gone, so nothing manages them, but the processes are alive and
may still be writing to storage and serving traffic if the network partition is partial.

This is why `kubectl delete pod --force` on a `NotReady` node is dangerous for stateful
workloads (Chapter 17): you are telling the API server the pod is gone so a replacement can
start, while the original may still be running with the same volume attached.

Common causes, in rough order:

- The node lost network connectivity to the API server.
- The kubelet crashed or was OOM-killed.
- The node ran out of disk and the kubelet cannot function.
- The container runtime (containerd) died.
- The VM was terminated by the cloud provider.

Investigate with a node debug pod (Chapter 18), since SSH may not be available:

```bash
kubectl debug node/NODE -it --image=nicolaka/netshoot -- chroot /host bash
```

```bash
systemctl status kubelet
journalctl -u kubelet --since '15 min ago' | tail -50
crictl ps
df -h
free -m
dmesg -T | tail -30
```

The kubelet's journal is usually explicit about why it is unhappy.

## Taints and node lifecycle

```bash
kubectl get nodes -o custom-columns='NAME:.metadata.name,TAINTS:.spec.taints[*].key'
```

```
k8sbook-control-plane   node-role.kubernetes.io/control-plane
k8sbook-worker          <none>
k8sbook-worker2         <none>
```

The control-plane taint is why your workloads land on workers without asking.

Taints applied automatically by the node controller are worth recognising, because they
explain sudden mass rescheduling:

| Taint | When |
|---|---|
| `node.kubernetes.io/not-ready` | Node `NotReady` |
| `node.kubernetes.io/unreachable` | Node controller cannot reach it |
| `node.kubernetes.io/memory-pressure` | Memory pressure |
| `node.kubernetes.io/disk-pressure` | Disk pressure |
| `node.kubernetes.io/unschedulable` | Cordoned |

The first two are `NoExecute`, which is what triggers the 5-minute eviction above. Every pod
gets a default 300-second toleration for them; lower it for workloads that must fail over
faster, at the cost of more churn during transient blips:

```yaml
tolerations:
  - key: node.kubernetes.io/unreachable
    operator: Exists
    effect: NoExecute
    tolerationSeconds: 60
```

### Cordon and drain

```bash
kubectl cordon NODE
```

```
k8sbook-worker2   Ready,SchedulingDisabled
```

Cordoning stops *new* pods scheduling there; existing pods keep running. It is the safe first
step before maintenance.

```bash
kubectl drain NODE --ignore-daemonsets --delete-emptydir-data
```

Drain cordons **and** evicts. The flags are almost always required: DaemonSet pods cannot be
rescheduled elsewhere so drain refuses without `--ignore-daemonsets`, and anything with an
`emptyDir` needs explicit acknowledgement that its data will be lost.

Drain uses the **Eviction API**, so it respects PodDisruptionBudgets — a drain that hangs is
usually a PDB doing its job (Chapter 25). Check:

```bash
kubectl get pdb -A
```

```bash
kubectl uncordon NODE
```

## Control plane health

```bash
kubectl get --raw='/livez'
```

```
ok
```

```bash
kubectl get --raw='/readyz?verbose'
```

```
[+]ping ok
[+]log ok
[+]etcd ok
[+]etcd-readiness ok
[+]informer-sync ok
...
```

The `?verbose` form lists every check individually, which is the fastest way to see *which*
subsystem is unhealthy. `[-]etcd failed` is a very different problem from a failing admission
webhook.

On self-managed clusters, the components are static pods (Chapter 3):

```bash
kubectl get pods -n kube-system -l tier=control-plane
```

```bash
kubectl logs -n kube-system kube-apiserver-NODE --tail=100
```

And when the API server is down entirely, you cannot use `kubectl` at all — go to the node:

```bash
crictl ps -a | grep apiserver
crictl logs CONTAINER_ID
cat /etc/kubernetes/manifests/kube-apiserver.yaml
```

The kubelet watches that directory, so **fixing the manifest file fixes the API server**. That
is the bootstrap escape hatch, and it is the reason static pods exist.

## etcd

The only stateful component, and the one whose failure is unrecoverable without a backup.

```bash
kubectl -n kube-system exec etcd-k8sbook-control-plane -- etcdctl \
  --endpoints=https://127.0.0.1:2379 \
  --cacert=/etc/kubernetes/pki/etcd/ca.crt \
  --cert=/etc/kubernetes/pki/etcd/server.crt \
  --key=/etc/kubernetes/pki/etcd/server.key \
  endpoint health
```

Back it up:

```bash
etcdctl snapshot save /backup/etcd-$(date +%F).db
```

Things that actually go wrong with etcd:

**Slow disk.** etcd fsyncs every write. On slow storage, `etcd_disk_wal_fsync_duration_seconds`
climbs and *everything* in the cluster gets slower — API calls, scheduling, controller
reactions. The symptom looks like a broken cluster; the cause is a disk. Watch this metric.

**Database size.** The default quota is 2 GiB. Exceeding it puts etcd into a maintenance mode
where it **rejects all writes** — the cluster becomes read-only, which is a spectacular
failure. Causes: many large objects (Secrets, ConfigMaps), or an unbounded controller creating
objects. Defragment and raise the quota if needed.

**Quorum loss.** With 3 members you can lose 1. Lose 2 and the cluster stops accepting writes
until quorum is restored. Odd member counts, spread across failure domains.

On managed clusters (EKS/GKE/AKS) none of this is yours — which is a substantial part of what
you are paying for.

## Certificates

Kubernetes uses TLS everywhere, and kubeadm-issued client certificates expire after **one
year**. An expired certificate presents as sudden, total authentication failure:

```
Unable to connect to the server: x509: certificate has expired or is not yet valid
```

```bash
kubeadm certs check-expiration
```

```bash
kubeadm certs renew all
```

Kubelet client certificates usually auto-rotate; the control plane's do not unless you upgrade
regularly (an upgrade renews them as a side effect). A cluster that has run untouched for
thirteen months is a cluster about to fail, and this is a genuinely common cause of "the
cluster died and nothing changed".

## Version skew

```bash
kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name}{" kubelet="}{.status.nodeInfo.kubeletVersion}{" runtime="}{.status.nodeInfo.containerRuntimeVersion}{"\n"}{end}'
```

```
k8sbook-control-plane kubelet=v1.37.0 runtime=containerd://2.3.4
k8sbook-worker        kubelet=v1.37.0 runtime=containerd://2.3.4
k8sbook-worker2       kubelet=v1.37.0 runtime=containerd://2.3.4
```

The supported skew: kubelet may be up to **3 minor versions** behind the API server, never
ahead. `kubectl` may be one minor version either side.

Upgrade order is control plane first, then nodes, one minor version at a time. Skipping minor
versions is unsupported and will bite you on API removals.

Which brings up the thing that actually breaks upgrades: **removed APIs**. Kubernetes removes
deprecated API versions on a schedule, and manifests using them fail to apply after an
upgrade. Check before upgrading:

```bash
kubectl get --raw /metrics | grep apiserver_requested_deprecated_apis
```

Tools like `pluto` and `kubent` scan manifests and live clusters for deprecated APIs. Run one
before every upgrade.

## Namespace stuck `Terminating`

A namespace that will not delete is almost always a **finalizer** on some object inside it, or
an unavailable APIService blocking enumeration.

```bash
kubectl get namespace NS -o jsonpath='{.status.conditions}' | python3 -m json.tool
```

The conditions name the blocking resource explicitly. Check for broken aggregated APIs, which
is a common cause:

```bash
kubectl get apiservice | grep -v True
```

An unavailable APIService — often a metrics or webhook service whose backing pods are gone —
blocks namespace deletion cluster-wide.

The `kubectl patch` to strip finalizers is widely circulated and **should be a last resort**:
it deletes the namespace object while leaving the resources it contained orphaned, and any
external resources those finalizers were meant to clean up (cloud load balancers, volumes)
leak silently.

## Try it

Check control plane health in detail:

```bash
kubectl get --raw='/readyz?verbose' | head -12
```

```bash
kubectl get --raw='/livez'
```

Look at node conditions and taints:

```bash
kubectl get nodes -o custom-columns='NAME:.metadata.name,TAINTS:.spec.taints[*].key,READY:.status.conditions[?(@.type=="Ready")].status'
```

Cordon a node and watch the status change, then reverse it:

```bash
kubectl cordon k8sbook-worker2 && kubectl get node k8sbook-worker2
```

```
k8sbook-worker2   Ready,SchedulingDisabled
```

```bash
kubectl uncordon k8sbook-worker2 && kubectl get node k8sbook-worker2
```

Try a drain, and see it move pods:

```bash
kubectl drain k8sbook-worker2 --ignore-daemonsets --delete-emptydir-data --timeout=120s
```

```bash
kubectl get pods -l app=pingd -o wide
```

Everything has moved off `worker2`. Bring it back:

```bash
kubectl uncordon k8sbook-worker2
```

Note the pods do **not** move back — the scheduler does not rebalance (Chapter 3). Force
redistribution with a restart:

```bash
kubectl rollout restart deployment/pingd && kubectl rollout status deployment/pingd
```

```bash
kubectl get pods -l app=pingd -o wide
```

Get onto a node without SSH:

```bash
kubectl debug node/k8sbook-worker -it --image=busybox:1.37 --quiet -- chroot /host sh -c 'uname -a; df -h /var/lib | tail -1'
```

Check version skew and deprecated API usage:

```bash
kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.status.nodeInfo.kubeletVersion}{"\n"}{end}'
```

```bash
kubectl get --raw /metrics | grep -c apiserver_requested_deprecated_apis || echo "none requested"
```

Clean up node debugger pods:

```bash
kubectl get pods -o name | grep node-debugger | xargs -r kubectl delete --force --grace-period=0
```

## Takeaways

- `NotReady` at ~40 s, pods rescheduled at ~5 min. **In between, the containers are still
  running** on the unreachable node — which is why force-deleting stateful pods is dangerous.
- The node controller's `NoExecute` taints explain sudden mass rescheduling. Every pod
  tolerates them for 300 s by default.
- `cordon` stops new scheduling; `drain` also evicts and respects PDBs. A hanging drain is
  usually a PDB.
- **Pods do not move back after uncordon** — the scheduler never rebalances. Use `rollout
  restart`.
- `/readyz?verbose` names the failing control-plane subsystem.
- Static pod manifests on disk are the escape hatch when the API server is down.
- etcd fails in three ways: **slow disk** (cluster-wide latency), **2 GiB quota exceeded**
  (all writes rejected), and quorum loss. Back it up.
- **kubeadm certificates expire after one year** and present as total auth failure.
- Kubelets may be 3 minor versions behind the API server, never ahead. Scan for removed APIs
  before upgrading.
- A stuck `Terminating` namespace is a finalizer or a broken APIService. Stripping finalizers
  leaks real resources.

---

Previous: [Chapter 20 — Resources and eviction](20-resources-and-eviction.md) ·
Next: [Chapter 22 — Cookbook](22-cookbook.md)

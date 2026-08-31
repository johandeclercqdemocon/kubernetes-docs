# Appendix A — kubectl cheatsheet

**⚠️** marks anything that deletes data or is hard to undo.

## Triage sequence

```bash
kubectl get pods -o wide                       # STATUS and READY are different facts
kubectl describe pod POD                       # includes EVENTS — usually the answer
kubectl logs POD --previous; kubectl logs POD  # try both
kubectl get events --sort-by=.lastTimestamp | tail -20
```

## Getting and formatting

```bash
kubectl get pods -o wide
kubectl get pods -A
kubectl get pods -l app=pingd --show-labels
kubectl get pods --field-selector status.phase=Running
kubectl get pods --sort-by=.metadata.creationTimestamp
kubectl get pod POD -o yaml
kubectl get pods -o name                       # feeds other commands

kubectl get pods -o custom-columns='NAME:.metadata.name,NODE:.spec.nodeName,QOS:.status.qosClass'
kubectl get pod POD -o jsonpath='{.spec.nodeName}{"\n"}'
kubectl get pods -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.status.podIP}{"\n"}{end}'
```

## Diagnostic one-liners

```bash
# why did it die
kubectl get pod POD -o jsonpath='exit={.status.containerStatuses[0].lastState.terminated.exitCode} reason={.status.containerStatuses[0].lastState.terminated.reason} restarts={.status.containerStatuses[0].restartCount}{"\n"}'

# scheduled or not (Pending triage)
kubectl get pod POD -o jsonpath='{.spec.nodeName}{"\n"}'

# has my change been seen
kubectl get deploy NAME -o jsonpath='gen={.metadata.generation} observed={.status.observedGeneration}{"\n"}'

# controller opinion
kubectl get deploy NAME -o jsonpath='{range .status.conditions[*]}{.type}={.status} {.reason}{"\n"}{end}'

# service backends — check conditions, not just addresses
kubectl get endpointslice -l kubernetes.io/service-name=SVC \
  -o custom-columns='ADDR:.endpoints[*].addresses[0],READY:.endpoints[*].conditions.ready'

# node pressure
kubectl get nodes -o custom-columns='NAME:.metadata.name,MEM:.status.conditions[?(@.type=="MemoryPressure")].status,DISK:.status.conditions[?(@.type=="DiskPressure")].status,READY:.status.conditions[?(@.type=="Ready")].status'

# requests vs allocatable (what the scheduler sees)
kubectl describe node NODE | sed -n '/Allocated resources/,/Events/p'
```

## Logs

```bash
kubectl logs -f --tail=100 --timestamps deploy/pingd
kubectl logs POD -c CONTAINER
kubectl logs POD --previous                    # the instance that crashed
kubectl logs -l app=pingd --prefix --tail=20
kubectl logs POD --since=10m
```

## Getting inside

```bash
kubectl exec -it POD -- sh
kubectl exec POD -- env | sort

# no shell in the image (distroless):
kubectl debug POD --image=nicolaka/netshoot --target=CONTAINER -it -- bash
#   then: ps -o pid,comm | ls /proc/1/root/ | cat /proc/1/environ | tr '\0' '\n'

# crash-looping (nothing to attach to):
kubectl debug POD --copy-to=probe --container=api -- sleep 3600

# the node itself (⚠️ privileged, host fs at /host):
kubectl debug node/NODE -it --image=nicolaka/netshoot -- chroot /host bash

kubectl cp POD:/path/file ./file               # needs tar in the image
kubectl port-forward deploy/pingd 8080:8000    # bypasses Service, DNS, Ingress
```

## Networking checks

```bash
kubectl run t --rm -it --restart=Never --image=nicolaka/netshoot -- bash
kubectl run t --rm -it --restart=Never --image=nicolaka/netshoot -- nslookup pingd
kubectl run t --rm -it --restart=Never --image=curlimages/curl:8.11.1 -- curl -sS http://pingd/

kubectl exec POD -- cat /etc/resolv.conf       # 127.0.0.11? ndots:5?
kubectl debug POD --image=nicolaka/netshoot -it -- ss -tln   # 0.0.0.0 or 127.0.0.1?
kubectl get networkpolicy -A
```

## Workload management

```bash
kubectl apply -f manifest.yaml
kubectl apply -k overlays/production
kubectl diff -f manifest.yaml                  # before applying
kubectl apply -f manifest.yaml --dry-run=server

kubectl rollout status deployment/NAME --timeout=300s   # exits non-zero on failure
kubectl rollout history deployment/NAME
kubectl rollout undo deployment/NAME            # ⚠️ desyncs from git
kubectl rollout restart deployment/NAME         # graceful restart / rebalance
kubectl rollout pause|resume deployment/NAME

kubectl scale deployment NAME --replicas=5
kubectl set image deployment/NAME api=IMAGE:TAG
kubectl set env deployment/NAME KEY=value
kubectl annotate deployment/NAME kubernetes.io/change-cause="Deploy 1.4.2 (a1b2c3d)"
```

## Nodes

```bash
kubectl top nodes; kubectl top pods --containers        # needs metrics-server
kubectl cordon NODE
kubectl drain NODE --ignore-daemonsets --delete-emptydir-data
kubectl uncordon NODE                          # note: pods do NOT move back
kubectl taint nodes NODE key=value:NoSchedule
kubectl taint nodes NODE key-                  # remove
```

## API discovery

```bash
kubectl api-resources
kubectl api-resources --namespaced=false
kubectl api-versions
kubectl explain deployment.spec.strategy
kubectl explain pod.spec.containers.resources --recursive
```

## RBAC

```bash
kubectl auth can-i create deployments -n production
kubectl auth can-i --list --as=system:serviceaccount:NS:SA
kubectl auth can-i get secrets --as=system:serviceaccount:default:default
kubectl auth whoami

kubectl create rolebinding team-a-edit --clusterrole=edit --group=team-a -n team-a
```

## Contexts and namespaces

```bash
kubectl config get-contexts
kubectl config current-context                 # check before every production command
kubectl config use-context CLUSTER
kubectl config set-context --current --namespace=myapp
```

## Cleanup

```bash
kubectl delete -f manifest.yaml
kubectl delete pod POD --force --grace-period=0    # ⚠️ risky for StatefulSets
kubectl delete pods --field-selector status.phase=Failed
kubectl delete namespace NS                        # ⚠️ deletes everything in it
```

## Status reference

| STATUS | Meaning |
|---|---|
| `Pending` + no `nodeName` | Scheduler could not place it |
| `Pending` + `nodeName` | Kubelet working: image pull, volumes, init |
| `Running` + `0/1` | Readiness failing |
| `ImagePullBackOff` | Cannot fetch image |
| `CreateContainerConfigError` | Missing ConfigMap/Secret |
| `CrashLoopBackOff` *or* `Error` | Container keeps exiting — **trust restart count** |
| `Terminating` (stuck) | Finalizer, grace period, or gone node |
| `Evicted` | Node pressure |

## Exit codes

`0` clean · `1` app error · `126` not executable · `127` not found ·
**`137` SIGKILL — OOM or ignored SIGTERM** · `139` SIGSEGV · `143` SIGTERM

Signals are 128 + N. **Exit 137 is a more reliable OOM signal than `reason: OOMKilled`.**

## cgroup / node inspection

```bash
kubectl debug node/NODE -it --image=busybox:1.37 -- chroot /host sh
# then:
journalctl -u kubelet --since '15 min ago' | tail -50
crictl ps; crictl logs CONTAINER_ID
dmesg -T | grep -i 'memory cgroup out of memory'
df -h; free -m
cat /etc/kubernetes/manifests/kube-apiserver.yaml   # static pod escape hatch
```

## Control plane

```bash
kubectl get --raw='/livez'
kubectl get --raw='/readyz?verbose'
kubectl get pods -n kube-system -l tier=control-plane
kubectl get --raw "/api/v1/nodes/NODE/proxy/metrics/resource" | grep '^container'
kubeadm certs check-expiration                 # one-year cliff
```

---

[Back to contents](../README.md) · Next: [Appendix B — Glossary](b-glossary.md)

# Runbook: Cilium is broken

## Symptoms

Pods stuck in `ContainerCreating`, DNS resolution failing cluster-wide, `cilium status`
reporting unready daemonsets, or Services unreachable from inside the cluster.

## Why you can always recover

The kube-apiserver runs on **host networking** at `192.168.178.16:6443`. It stays reachable with
Cilium completely broken, so `kubectl` and `helm` keep working from your laptop. `talosctl` does
not depend on the CNI either.

## Immediate recovery

Reinstall Cilium directly with Helm, bypassing ArgoCD:

    helm repo add cilium https://helm.cilium.io
    helm upgrade --install cilium cilium/cilium --version 1.18.0 \
      -n kube-system -f infrastructure/cilium/values.yaml
    cilium status --wait

This leaves a Helm release object that ArgoCD does not know about. Once the cluster is healthy,
delete it so there is one source of truth again:

    kubectl delete secret -n kube-system -l 'owner=helm,name=cilium'

## If a bad commit caused it

    git revert <bad-sha>
    git push
    kubectl -n argocd patch application cilium --type merge -p '{"operation":{"sync":{"revision":"main"}}}'

## If the Application was deleted

Cilium's Application deliberately carries no `resources-finalizer.argocd.argoproj.io`, so
deleting it does **not** delete the CNI. Recreate it:

    kubectl apply -f cluster/applications/cilium.yaml

## Node-level inspection

    talosctl -n 192.168.178.16 dmesg | tail -50
    talosctl -n 192.168.178.16 services
    talosctl -n 192.168.178.16 logs kubelet | tail -50

## Do not

Do not enable `prune: true` on Cilium's Application. Pruning a CNI resource because of a
transient render failure takes down cluster networking.

Do not add a finalizer to Cilium's Application.

Do not paste an unredacted `dyff` of rendered Cilium output anywhere. The chart generates Hubble
TLS certificates at template time, so the diff of a Secret's `data` field is a private key. Pipe
through `sed -E 's/[A-Za-z0-9+\/]{60,}=*/<REDACTED>/g'` first.

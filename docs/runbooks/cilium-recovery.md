# Runbook: Cilium is broken

Symptoms: pods stuck `ContainerCreating`, cluster-wide DNS failing, `cilium status` unready, or
in-cluster Services unreachable.

## Why recovery always works
The kube-apiserver runs on host networking at `192.168.178.16:6443` — reachable with Cilium fully
broken, so `kubectl`/`helm`/`talosctl` keep working.

## Immediate recovery (Helm, bypassing ArgoCD)
    helm repo add cilium https://helm.cilium.io
    helm upgrade --install cilium cilium/cilium --version 1.20.1 -n kube-system -f infrastructure/cilium/values.yaml
    cilium status --wait
Then remove the ArgoCD-invisible Helm release so there's one source of truth:
`kubectl delete secret -n kube-system -l 'owner=helm,name=cilium'`.

## Bad commit / deleted Application
    git revert <sha> && git push
    kubectl -n argocd patch application cilium --type merge -p '{"operation":{"sync":{"revision":"main"}}}'
Cilium's Application carries **no finalizer**, so deleting it doesn't delete the CNI — recreate with
`kubectl apply -f cluster/applications/cilium.yaml`.

## Node-level inspection
    talosctl -n 192.168.178.16 dmesg | tail -50
    talosctl -n 192.168.178.16 services

## Config changes need a manual rollout restart
Syncing `infrastructure/cilium/values.yaml` only updates the `cilium-config` ConfigMap; the chart
sets no config-checksum, so pods aren't restarted and the new setting stays inactive until:
    kubectl -n kube-system rollout restart deployment/cilium-operator daemonset/cilium
Operator restart is datapath-safe; the agent restart briefly drops each node's datapath (short blip
as the DaemonSet rolls). Same step activates Hubble metrics (#6).

## Do not
- `prune: true` on Cilium's Application (a transient render failure would take down networking).
- Add a finalizer to Cilium's Application.
- Paste an unredacted `dyff` of rendered Cilium output — it contains Hubble TLS private keys; pipe
  through `sed -E 's/[A-Za-z0-9+\/]{60,}=*/<REDACTED>/g'` first.

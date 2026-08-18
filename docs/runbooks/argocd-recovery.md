# Runbook: ArgoCD is broken

## Symptoms

Applications stop reconciling, the repo-server crashlooping, ArgoCD `OutOfSync` against itself,
or a bad commit to `infrastructure/argocd` that broke the deployment that would fix it.

## Recovery: re-apply from Git

ArgoCD manages itself, but the bootstrap path never depends on ArgoCD running:

    kustomize build --enable-helm bootstrap/argocd | kubectl apply --server-side -f -
    kubectl -n argocd rollout status deploy/argocd-server --timeout=300s

If a bad commit is the cause, revert first so ArgoCD does not immediately re-break itself:

    git revert <bad-sha>
    git push

## Recovery: full reinstall

Safe as long as no Application carries a finalizer whose resources you cannot afford to lose.
Check first:

    kubectl -n argocd get applications -o custom-columns='NAME:.metadata.name,FINALIZERS:.metadata.finalizers'

Deleting the namespace deletes the Applications. Any Application **with** a finalizer will
cascade-delete its managed resources. Cilium has no finalizer precisely so this is survivable.

    kubectl delete namespace argocd --wait=true
    kustomize build --enable-helm bootstrap/argocd | kubectl apply --server-side -f -
    kubectl -n argocd rollout status deploy/argocd-server --timeout=300s
    kubectl apply -f cluster/root.yaml

The Argo CRDs are cluster-scoped and survive namespace deletion.

## Symptom: permanently OutOfSync against itself

This means `bootstrap/argocd` and `infrastructure/argocd` render differently, which should be
impossible — `bootstrap/` is nothing but a reference. Verify:

    kustomize build --enable-helm bootstrap/argocd > /tmp/a.yaml
    kustomize build --enable-helm infrastructure/argocd > /tmp/b.yaml
    dyff between /tmp/a.yaml /tmp/b.yaml

Expected: zero differences. Anything else means `bootstrap/argocd/kustomization.yaml` grew
content it should not have.

## Symptom: CRD apply fails with "annotation too long"

Client-side apply was used somewhere. Every apply of ArgoCD needs `--server-side`, and ArgoCD's
own Application needs `ServerSideApply=true` in its `syncOptions`.

## Symptom: "I changed something by hand and ArgoCD did not revert it"

Usually correct behaviour, not a bug.

Every Application in this repository syncs with `ServerSideApply=true`. Under server-side apply
ArgoCD owns only the fields it actually declares, tracked through the object's `managedFields`.
A field introduced by a different manager — a label you added with `kubectl label`, an annotation
from another controller — is an *extra* field rather than a divergence from desired state, so
there is nothing for self-heal to reconcile. The Application stays `Synced`, correctly.

Verified on this cluster 2026-08-18: `kubectl -n kube-system label daemonset cilium
drift-test=true` was never reverted, while the Cilium Application reported `Synced` the whole time.

To confirm self-heal genuinely works, drift a field the rendered manifest declares:

    kubectl -n kube-system patch ds cilium --type merge \
      -p '{"spec":{"updateStrategy":{"rollingUpdate":{"maxUnavailable":1}}}}'

Within about a minute it returns to `2`. `updateStrategy` is a safe choice because it only affects
how future template changes roll out — changing it restarts no pods. Avoid drifting anything
inside `spec.template`: that field *is* declared, so it would be reverted, but the revert triggers
a rolling restart of the CNI on a live cluster.

To see who owns a field:

    kubectl -n kube-system get ds cilium --show-managed-fields -o yaml | grep -A5 'manager: argocd'

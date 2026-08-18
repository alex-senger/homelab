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

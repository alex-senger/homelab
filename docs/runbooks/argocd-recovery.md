# Runbook: ArgoCD is broken

Symptoms: Applications stop reconciling, repo-server crashlooping, ArgoCD OutOfSync against itself,
or a bad `infrastructure/argocd` commit that broke the deployment that would fix it.

## Re-apply from Git (bootstrap never needs ArgoCD running)
    kustomize build --enable-helm bootstrap/argocd | kubectl apply --server-side -f -
    kubectl -n argocd rollout status deploy/argocd-server --timeout=300s
If a bad commit caused it, `git revert <sha> && git push` first so it doesn't re-break itself.

## Full reinstall
Safe unless an Application with a finalizer would cascade-delete resources you can't lose (check:
`kubectl -n argocd get applications -o custom-columns='NAME:.metadata.name,FIN:.metadata.finalizers'`
— Cilium has none precisely so this is survivable). CRDs are cluster-scoped and survive.
    kubectl delete namespace argocd --wait=true
    kustomize build --enable-helm bootstrap/argocd | kubectl apply --server-side -f -
    kubectl -n argocd rollout status deploy/argocd-server --timeout=300s
    kubectl apply -f cluster/root.yaml

## OutOfSync against itself
`bootstrap/argocd` and `infrastructure/argocd` should render identically:
    dyff between <(kustomize build --enable-helm bootstrap/argocd) <(kustomize build --enable-helm infrastructure/argocd)
Nonzero diff → `bootstrap/argocd/kustomization.yaml` grew content it shouldn't have.

## Other
- CRD apply "annotation too long" → client-side apply was used; every ArgoCD apply needs
  `--server-side`, and its own Application needs `ServerSideApply=true`.
- "I changed something by hand and ArgoCD didn't revert it" → expected: under SSA, ArgoCD owns only
  the fields it declares, so an extra label/annotation isn't drift. To test selfheal, drift a
  *declared* field (`kubectl -n kube-system patch ds cilium --type merge -p
  '{"spec":{"updateStrategy":{"rollingUpdate":{"maxUnavailable":1}}}}'` → reverts within ~1 min).
  Don't drift anything under `spec.template` — the revert restarts pods.

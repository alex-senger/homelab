# Bootstrap

How to bring the ArgoCD layer of the `roastery` cluster up from nothing.

## Prerequisites

- `kubectl` with a working context for `roastery`
- `kustomize` v5 (standalone — `kubectl apply -k` cannot inflate Helm charts)
- The repository must be **public**. ArgoCD clones over anonymous HTTPS; a private
  repository needs a credential Secret, which this cluster deliberately does not have.
- The age private key at the path named by `SOPS_AGE_KEY_FILE`, needed only to regenerate Talos
  machine configs — not to bootstrap Kubernetes.

## Bootstrap

Talos runs with `cni: none`, so on a fresh cluster every node stays `NotReady` until a CNI exists.
ArgoCD cannot schedule without one, and therefore cannot be the thing that installs it. Cilium
goes first.

    kustomize build --enable-helm bootstrap/cilium | kubectl apply --server-side -f -
    kubectl wait --for=condition=Ready nodes --all --timeout=300s
    kustomize build --enable-helm bootstrap/argocd | kubectl apply --server-side -f -
    kubectl -n argocd rollout status deploy/argocd-server --timeout=300s
    kubectl apply -f cluster/root.yaml

Both bootstrap directories contain nothing but a reference to the corresponding component under
`infrastructure/`, so there is exactly one definition of each in the repository and the ArgoCD
Applications adopt precisely these resources on the first root sync.

`--server-side` is required, not optional: ArgoCD's CRDs exceed the 262 144-byte
`last-applied-configuration` annotation that client-side apply writes. If the apply reports
field-manager conflicts from a previous installation, add `--force-conflicts`.

`kubectl apply -k` cannot be substituted — it has no `--enable-helm` flag and so cannot
inflate the chart.

## Verify

    kubectl -n argocd get applications

All Applications should reach `Synced` and `Healthy`.

## Admin access

    kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d
    kubectl -n argocd port-forward svc/argocd-server 8080:80

Log in at `http://localhost:8080` as `admin`. TLS terminates at the Gateway from
sub-project #3 onward; until then ArgoCD serves plaintext internally and is reached
by port-forward only.

## Why bootstrap and infrastructure cannot drift

`bootstrap/argocd/kustomization.yaml` contains nothing but a reference to
`infrastructure/argocd`. There is exactly one definition of ArgoCD in this repository, so the
usual self-management failure — a bootstrap manifest that diverges from the GitOps-managed one,
leaving the Application permanently `OutOfSync` — cannot occur.

# Network policies (Cilium ingress containment)

`infrastructure/network-policies/`, app `network-policies`, wave 26. One `CiliumNetworkPolicy`
per protected namespace, all named `default-deny-ingress`.

## Model

Each CNP sets `endpointSelector: {}` (all endpoints in the namespace) with no egress rules —
Cilium flips every selected endpoint to default-deny **ingress** only; egress stays open.
Callers are matched by the automatic `k8s:io.kubernetes.pod.namespace` label (`fromEndpoints`)
or by reserved identities (`fromEntities: [ingress, host, kube-apiserver]`). One PR merged
per namespace; all 7 are live.

## Allow matrix

| Namespace | Intra-ns | Allowed ingress |
|---|---|---|
| openbao | `fromEndpoints: [{}]` | `external-secrets` → :8200 (Vault API) |
| external-secrets | `fromEndpoints: [{}]` | `fromEntities: [kube-apiserver]` (webhook, all ports) |
| cnpg-system | `fromEndpoints: [{}]` | `fromEntities: [kube-apiserver]` (webhook, all ports) |
| databases | `fromEndpoints: [{}]` | `authelia`, `nextcloud` → :5432; `cnpg-system` → all ports (operator); `monitoring` → :9187 (VMAgent) |
| garage | `fromEndpoints: [{}]` | `longhorn-system`, `databases` → :3900 (S3 API) |
| authelia | `fromEndpoints: [{}]` | `fromEntities: [ingress, host]` → :9091 |
| argocd | `fromEndpoints: [{}]` | `fromEntities: [ingress, host]` → :80 |

`longhorn-system` is an allowed *source* to garage but is not itself a protected namespace.

## Verification

Drops in a namespace:

    kubectl -n kube-system exec ds/cilium -- hubble observe --namespace <ns> --verdict DROPPED --last 100

Gateway-fronted service (authelia/argocd), from a curl pod:

    curl -sk --connect-to <host>:443:cilium-gateway-external.gateway.svc.cluster.local:443 https://<host>/...

Expect `200`.

Containment probe — throwaway busybox pod in a public namespace (`nextcloud`/`minecraft`):

    kubectl run probe --rm -it --image=busybox --restart=Never -n nextcloud -- \
      nc -w3 -z <svc>.<protected-ns>.svc.cluster.local <port>

Expect exit code `1` (denied), confirmed by `hubble observe --namespace <protected-ns> --verdict DROPPED`
showing `Policy denied DROPPED`.

## Rollback

`git revert` the offending PR and merge — ArgoCD removes or reverts the CNP on next sync. No
kubectl streaming required.

## Gotchas (live-learned)

1. **Gateway source identity is `ingress`, not `host`.** Cilium Gateway/Envoy L7-proxied traffic
   to backends carries the reserved `ingress` identity. A `host`-only allow on authelia caused a
   live SSO 503; fixed by allowing `[ingress, host]`. Confirm with
   `hubble observe --namespace <ns> --verdict DROPPED` — the denied source shows `(ingress)`.
2. **ArgoCD lags ~1 poll behind after merge.** Force pickup:
   `kubectl -n argocd annotate application network-policies argocd.argoproj.io/refresh=normal --overwrite`
3. **`kubectl get backup` is ambiguous** — Longhorn's `backups.longhorn.io` shadows CNPG's. Use
   `kubectl -n databases get backups.postgresql.cnpg.io`.
4. **CNPG S3 backup traffic originates in `databases`, not `cnpg-system`** — the WAL/base-backup
   sidecar runs in the instance pod, which is why garage allows `databases` → :3900. A live
   on-demand backup completed through the policy, confirming this.

## Residual risks (accepted, phase 1)

- Egress is open on every namespace — exfil/C2 is still possible. Phase 2: egress lockdown on
  the public-facing apps.
- Intra-namespace traffic is fully allowed (`fromEndpoints: [{}]`) with no further segmentation.
- `longhorn-system` is an allowed source to garage but has no CNP of its own.

## Phase 2 — egress lockdown (minecraft, website)

Model: egress-only CNP (`default-deny-egress`) flips the app to default-deny **egress**;
ingress stays untouched (both are public apps). Egress policies MUST allow DNS.

Allow-lists:

| Namespace | Allowed egress |
|---|---|
| website | CoreDNS :53 only |
| minecraft | CoreDNS :53 + `world:443` (Mojang session auth/skins + plugin fetches) |

Note: Nextcloud egress is intentionally **not** locked down — it needs broad HTTPS-to-world
anyway, so a default-deny egress policy would add high maintenance for marginal value; phase 1
already contains its ingress to the crown-jewel namespaces.

Verify: server/site healthy; minecraft `world:443` reachable (curl a Mojang endpoint from an
in-namespace pod) and non-443 egress denied (`hubble observe --namespace minecraft --verdict
DROPPED` shows `Policy denied DROPPED`).

# Network policies (Cilium containment)

`infrastructure/network-policies/`, app `network-policies`, wave 26.

## Model

A CNP with `endpointSelector: {}` and only ingress rules flips the namespace to default-deny
**ingress**; egress stays open. Callers are matched by the automatic
`k8s:io.kubernetes.pod.namespace` label (`fromEndpoints`) or reserved identities
(`fromEntities: [ingress, host, kube-apiserver]`). Kyverno (`generate-default-netpol-v2`) adds an
intra-namespace-only `default-deny-ingress` to every *new* namespace not in its exclude list; the
files here add allows on top.

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
| nextcloud | `fromEndpoints: [{}]` | `fromEntities: [ingress, host]` → :80, :7867 (notify_push) |
| cert-manager | `fromEndpoints: [{}]` | `fromEntities: [kube-apiserver, host]` → :10250 (webhook); `monitoring` → :9402 |
| trivy-system | Kyverno-generated | `monitoring` → operator :8080 |
| website | Kyverno-generated | `fromEntities: [ingress]` → :80 |
| minecraft | Kyverno-generated | `wg-ingress` → :25565 |

## Verification

Drops in a namespace:

    kubectl -n kube-system exec ds/cilium -- hubble observe --namespace <ns> --verdict DROPPED --last 100

Gateway-fronted service (authelia/argocd), from a curl pod:

    curl -sk --connect-to <host>:443:cilium-gateway-external.gateway.svc.cluster.local:443 https://<host>/...

Expect `200`.

Containment probe — throwaway busybox pod in a public namespace (`nextcloud`/`minecraft`); the
overrides satisfy restricted PSA:

    kubectl run probe --rm -it --image=docker.io/busybox:1.37 --restart=Never -n nextcloud \
      --override-type=strategic --overrides='{"spec":{"containers":[{"name":"probe","securityContext":{"runAsNonRoot":true,"runAsUser":65534,"allowPrivilegeEscalation":false,"capabilities":{"drop":["ALL"]},"seccompProfile":{"type":"RuntimeDefault"}}}]}}' \
      -- nc -w3 -z <svc>.<protected-ns>.svc.cluster.local <port>

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

## Residual risks (accepted)

- Egress is open everywhere except minecraft/website (below).
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

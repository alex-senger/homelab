# Runbook: External reach is broken

Covers the Let's Encrypt ClusterIssuers, the two Secrets that exist only on-cluster (never
committed), the WireGuard tunnel to the VPS, and how to debug a certificate stuck issuing.

## Symptoms

`https://<subdomain>.senger-solutions.com` unreachable from outside the LAN, a `Certificate`
stuck `Ready=False`, a `ClusterIssuer` not `Ready`, or the WireGuard tunnel to the VPS down.

## Out-of-band Secrets

This sub-project knowingly suspends the "whole cluster is in Git" property for two Secrets.
Neither is committed, encrypted or otherwise. External Secrets Operator (sub-project #5) adopts
both later; until then, this section is the only record of what exists outside Git and how to
recreate it.

### `cloudflare-api-token` (namespace `cert-manager`)

A Cloudflare API token scoped to **Zone → DNS → Edit** on the `senger-solutions.com` zone only.
Consumed by both ClusterIssuers' DNS-01 solver.

To recreate: generate a new scoped token in the Cloudflare dashboard, then

    export KUBECONFIG=/tmp/rk.yaml
    kubectl -n cert-manager create secret generic cloudflare-api-token \
      --from-literal=api-token='<PASTE_TOKEN>'

If the Secret already exists and you are rotating the token, delete it first
(`kubectl -n cert-manager delete secret cloudflare-api-token`) or add `--dry-run=client -o yaml |
kubectl apply -f -` to the command above.

### `wg-ingress-key` (namespace `wg-ingress`)

The persistent WireGuard private key for the cluster end of the VPS tunnel (`infrastructure/wg-ingress`).
Persistent and not per-pod: a rescheduled pod re-dials the VPS using the same key, so the VPS-side
peer configuration never needs to change.

To recreate:

    wg genkey | tee /tmp/wg-ingress.key | wg pubkey > /tmp/wg-ingress.pub
    export KUBECONFIG=/tmp/rk.yaml
    kubectl -n wg-ingress create secret generic wg-ingress-key \
      --from-file=privateKey=/tmp/wg-ingress.key
    shred -u /tmp/wg-ingress.key   # do not leave the private key on disk

The public key printed to `/tmp/wg-ingress.pub` must then be added to the VPS's WireGuard peer
config (out of scope here — see `vps/`). Rotating this key requires updating the VPS side too, or
the tunnel will not re-establish.

## Checking the WireGuard tunnel handshake

From inside the `wg-ingress` pod:

    export KUBECONFIG=/tmp/rk.yaml
    kubectl -n wg-ingress exec deploy/wg-ingress -- wg show

A healthy tunnel shows a `latest handshake` within the last `PersistentKeepalive` interval
(25s) and a nonzero `transfer` in both directions. No handshake at all usually means either the
VPS is down/unreachable, the VPS-side peer's allowed public key doesn't match this Secret's
public key, or outbound UDP to the VPS is being blocked. A handshake that appears and then goes
stale means the tunnel came up but traffic isn't flowing — check the VPS's `nftables` rules and
its `nginx stream` config.

## Debugging a stuck certificate

Start with the `Certificate` and work down to the ACME challenge:

    export KUBECONFIG=/tmp/rk.yaml
    kubectl -n <namespace> describe certificate <name>
    kubectl -n <namespace> get challenge
    kubectl -n <namespace> describe challenge <challenge-name>

Common causes surfaced in the `describe` output:

- **`ClusterIssuer` not `Ready`** — usually a bad or revoked `cloudflare-api-token`, or the ACME
  account registration itself failing. `kubectl describe clusterissuer letsencrypt-staging` (or
  `letsencrypt-prod`) shows the registration error directly.
- **Challenge stuck `pending`** — the DNS-01 TXT record was never created or hasn't propagated.
  Cloudflare token scope (must be DNS Edit on the right zone) is the most common cause of a token
  that authenticates but can't write the record.
- **Rate limited** — Let's Encrypt production has weekly limits per registered domain. This is
  why issuance is always proven against `letsencrypt-staging` first; only flip a `Certificate`'s
  `issuerRef.name` to `letsencrypt-prod` once staging reaches `Ready=True`.
- **Stale `Order`/`Challenge` left behind** — deleting the `Certificate`'s Secret
  (`kubectl -n <namespace> delete secret <secretName>`) forces cert-manager to re-request from
  scratch; safe to do any time on staging, do it deliberately on prod (rate limits).

## ArgoCD access

ArgoCD itself stays reachable only via `kubectl -n argocd port-forward svc/argocd-server 8080:443`
until sub-project #7. It is not exposed through this Gateway/tunnel path, so ArgoCD being
unreachable from outside is expected, not a symptom of anything in this runbook.

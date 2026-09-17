# Runbook: External reach is broken

LE ClusterIssuers (Cloudflare DNS-01), the two out-of-band Secrets, and the WireGuard tunnel to
the VPS.

## Symptoms
`https://<sub>.senger-solutions.com` unreachable from outside the LAN; a `Certificate` stuck
`Ready=False`; a `ClusterIssuer` not Ready; or the WG tunnel down.

## Out-of-band Secrets (never committed; ESO-adopted since #5)
- **`cloudflare-api-token`** (cert-manager): Cloudflare token scoped Zone→DNS→Edit on
  `senger-solutions.com`, used by both ClusterIssuers' DNS-01 solver.

      kubectl -n cert-manager create secret generic cloudflare-api-token --from-literal=api-token=<…>

- **`wg-ingress-key`** (wg-ingress): persistent WG private key for the cluster end of the VPS
  tunnel — a rescheduled pod re-dials with the same key.

      wg genkey | tee /tmp/wg.key | wg pubkey > /tmp/wg.pub
      kubectl -n wg-ingress create secret generic wg-ingress-key --from-file=privateKey=/tmp/wg.key
      shred -u /tmp/wg.key

  The public key must be on the VPS peer; rotating requires updating the VPS side too.

## WireGuard tunnel
    kubectl -n wg-ingress exec deploy/wg-ingress -- wg show
Healthy = a recent handshake + nonzero transfer both ways. No handshake → VPS down/unreachable,
peer pubkey mismatch, or outbound UDP blocked. Handshake then stale → check the VPS nftables + its
`nginx stream` SNI config.

## Stuck certificate
    kubectl -n <ns> describe certificate <name>
    kubectl -n <ns> get challenge && kubectl -n <ns> describe challenge <name>
- ClusterIssuer not Ready → bad/revoked Cloudflare token or ACME registration failing
  (`describe clusterissuer letsencrypt-prod`).
- Challenge pending → DNS-01 TXT never written/propagated (usually token scope).
- Rate limited → prove on `letsencrypt-staging` first; flip `issuerRef` to prod once staging is Ready.
- Stale Order/Challenge → delete the Certificate's Secret to force a fresh request (deliberate on
  prod — rate limits).

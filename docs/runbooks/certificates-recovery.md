# Runbook: Certificates

LE ClusterIssuers (Cloudflare DNS-01) issue the Gateway's wildcard certificate. DNS-01 needs no
inbound path, so it works on a LAN-only cluster.

## Out-of-band Secret (ESO-adopted)
**`cloudflare-api-token`** (cert-manager): token scoped Zone→DNS→Edit on `senger-solutions.com`,
in OpenBao at `secret/cert-manager/cloudflare-api-token`.

    kubectl -n cert-manager create secret generic cloudflare-api-token --from-literal=api-token=<…>

## Stuck certificate
    kubectl -n <ns> describe certificate <name>
    kubectl -n <ns> get challenge && kubectl -n <ns> describe challenge <name>
- ClusterIssuer not Ready → bad/revoked Cloudflare token or ACME registration failing
  (`describe clusterissuer letsencrypt-prod`).
- Challenge pending → DNS-01 TXT never written/propagated (usually token scope).
- Rate limited → prove on `letsencrypt-staging` first; flip `issuerRef` to prod once staging is Ready.
- Stale Order/Challenge → delete the Certificate's Secret to force a fresh request (deliberate on
  prod — rate limits).

## Host unreachable on the LAN
All hosts are LAN-only: the home DNS must resolve `<host>.senger-solutions.com` to `192.168.178.3`.
Check with `dig <host>.senger-solutions.com` from a LAN client; `openssl s_client -connect
192.168.178.3:443 -servername <host> </dev/null` shows the wildcard cert.

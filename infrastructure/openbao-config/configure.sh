#!/bin/sh
set -eu
export BAO_ADDR="${BAO_ADDR:-http://openbao.openbao.svc:8200}"
# BAO_TOKEN comes from the mounted root-token secret.
bao secrets enable -path=secret kv-v2 2>/dev/null || echo "kv already enabled"
bao auth enable kubernetes 2>/dev/null || echo "kubernetes auth already enabled"
bao write auth/kubernetes/config \
  kubernetes_host="https://${KUBERNETES_SERVICE_HOST}:${KUBERNETES_SERVICE_PORT}"
bao policy write eso-read /policies/eso-read.hcl
bao write auth/kubernetes/role/eso \
  bound_service_account_names=external-secrets \
  bound_service_account_namespaces=external-secrets \
  policies=eso-read \
  ttl=1h

# Authelia OIDC login (UI via port-forward, CLI via `bao login -method=oidc`).
# Skipped until secret/openbao/oidc is seeded and synced into openbao-oidc.
if [ -n "${OIDC_CLIENT_SECRET:-}" ]; then
  bao auth enable oidc 2>/dev/null || echo "oidc auth already enabled"
  bao auth tune -listing-visibility=unauth oidc/
  bao policy write admin /policies/admin.hcl
  bao write auth/oidc/config \
    oidc_discovery_url=https://auth.senger-solutions.com \
    oidc_client_id=openbao \
    oidc_client_secret="${OIDC_CLIENT_SECRET}" \
    default_role=admin
  bao write auth/oidc/role/admin @/policies/oidc-role.json
else
  echo "OIDC_CLIENT_SECRET unset; skipping oidc auth"
fi
echo "openbao config applied"

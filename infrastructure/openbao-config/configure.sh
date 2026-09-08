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
echo "openbao config applied"

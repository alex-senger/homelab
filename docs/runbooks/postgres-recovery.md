# PostgreSQL (CloudNativePG) — bring-up & recovery

#7c. `infrastructure/cnpg-operator` (app `cnpg-operator`, wave 12) + `infrastructure/postgres` (app
`postgres`, wave 21). Operator chart `cloudnative-pg` 0.29.0; shared single-instance `Cluster pg`
in ns `databases`, Postgres 17 on `longhorn-retain`. Per-app DB/role added by each consumer.

## Bring-up (after merge)
Seed each app's role password in OpenBao (never in Git), e.g.:
    kubectl exec -n openbao openbao-0 -- sh -c 'BAO_TOKEN=<root> bao kv put secret/databases/authelia password=<strong>'
→ ESO materialises a `kubernetes.io/basic-auth` Secret the cluster's `managed.roles` uses. Then let
ArgoCD sync `cnpg-operator` then `postgres` (hard-refresh if it lags).

## Verify
- Operator: `kubectl -n cnpg-system get pods` Running; `kubectl get crd | grep postgresql.cnpg.io` (11).
- Cluster: `kubectl -n databases get cluster pg` healthy, 1/1; PVC bound on `longhorn-retain`.
- Credential path end-to-end (a passwordless connect just hangs, proving nothing):

      PW=$(kubectl -n databases get secret authelia-db -o jsonpath='{.data.password}' | base64 -d)
      kubectl -n databases run psql-check --rm -it --restart=Never --image=ghcr.io/cloudnative-pg/postgresql:17 \
        --env=PGPASSWORD="$PW" -- psql -h pg-rw.databases.svc -U authelia -d authelia -c '\conninfo'

## Notes
- Operator mutates its webhook caBundles at runtime → `cluster/applications/cnpg-operator.yaml`
  `ignoreDifferences` (else OutOfSync). The `Cluster pg` app itself also ignores CNPG-defaulted
  spec fields (`cluster/applications/postgres.yaml`).
- Backups (WAL + PITR) via #7d — see backups-recovery.md.

## Failure modes
- Operator down → no DB/role reconciliation; running Postgres unaffected.
- Instance down (node reboot) → DB unavailable ~30–60s (single-instance trade-off); CNPG restarts it.
- ESO can't fetch the password → role/Secret not materialised; fails safe; re-`put` and resync.
- PVC lost → data lost; `longhorn-retain` guards accidental delete; recover via #7d PITR.

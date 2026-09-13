# PostgreSQL (CloudNativePG) — bring-up & recovery

Sub-project #7c. Components: `infrastructure/cnpg-operator` (app `cnpg-operator`,
wave 12) and `infrastructure/postgres` (app `postgres`, wave 21). Operator chart
`cloudnative-pg` 0.29.0 (app 1.30.0); shared single-instance `Cluster pg` in
namespace `databases`, Postgres 17 on `longhorn-retain`.

## One-time bring-up (operator steps, after merge)

1. **Create the authelia role password in OpenBao** (never in Git):

       kubectl exec -n openbao openbao-0 -- sh -c \
         'BAO_TOKEN=<root-or-admin-token> bao kv put secret/databases/authelia \
            password=<choose-a-strong-password>'

   The `ExternalSecret` `authelia-db` (ns `databases`) then materialises a
   `kubernetes.io/basic-auth` Secret (`username=authelia` + that password), which
   the cluster's `managed.roles` uses to set the role password.

2. Let ArgoCD sync `cnpg-operator` then `postgres` (or
   `kubectl -n argocd annotate app postgres argocd.argoproj.io/refresh=hard
   --overwrite` if it lags a reconcile behind the merge).

## Acceptance checks

- Operator: `kubectl -n cnpg-system get pods` Running; CRDs `kubectl get crd |
  grep postgresql.cnpg.io` present (11).
- Cluster: `kubectl -n databases get cluster pg` reports `Cluster in healthy
  state`, 1/1 ready; the PVC is bound on `longhorn-retain`.
- Database + role: `kubectl -n databases get database authelia` is applied; the
  role exists — from a psql session (below) `\du` shows `authelia`.
- Credential path: connect as the app role and confirm the OpenBao password works:

      kubectl -n databases exec -it pg-1 -- psql -U authelia -d authelia -c '\conninfo'

  (Or run a throwaway psql pod against `pg-rw.databases.svc`.) A successful
  connect proves OpenBao → ESO → managed.roles end to end.
- Metrics: the `pg` instance appears in Grafana (a `cnpg_*` / `up` series for
  namespace `databases`).

## Notes

- **Webhook drift is ignored, not fought.** The operator injects its serving CA
  into `cnpg-mutating-webhook-configuration` and
  `cnpg-validating-webhook-configuration` at runtime; `cluster/applications/cnpg-operator.yaml`
  lists them under `ignoreDifferences` so the app does not sit OutOfSync.
- **The app-side connection Secret is the consuming app's job.** #7c creates the
  database, the role, and the role password in `databases`. Authelia (#7b) adds
  its own `ExternalSecret` in the `authelia` namespace, pulling the same OpenBao
  path, to get a libpq connection Secret pointing at `pg-rw.databases.svc:5432`.

## Failure modes

- Operator down → no DB/role reconciliation; running Postgres unaffected;
  restarts under ArgoCD selfHeal.
- Postgres instance down (node reboot) → DB unavailable ~30–60s; CNPG restarts
  it; single-instance trade-off.
- ESO can't fetch the password → role/Secret not materialised; fails safe (no
  default password); re-`put` in OpenBao and let ESO resync.
- PVC lost → data lost; `longhorn-retain` prevents accidental delete; off-host
  backups + PITR arrive with #7d.

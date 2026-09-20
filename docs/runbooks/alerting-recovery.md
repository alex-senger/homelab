# Alerting (vmalert + Alertmanager) — bring-up & recovery

Part of `infrastructure/monitoring` (app `monitoring`, wave 20). vmalert evaluates the curated
`vmrule-roastery.yaml`; VMAlertmanager routes to email (Resend) and a webhook (healthchecks.io).

    VMSingle → vmalert → VMAlertmanager ─┬─ email  → Resend SMTP → inbox
    (metrics)  (VMRule)  (retain PVC)    └─ Watchdog → webhook → healthchecks.io (dead-man)

## Secrets (OpenBao → ESO)

`secret/alerting/email` → ExternalSecret `alertmanager-config` renders the full `alertmanager.yaml`
into a Secret consumed by VMAlertmanager `spec.configSecret`. Keys:

    recipient         inbox alerts go to
    resend-password   Resend API key (SMTP user is the literal "resend"; sender alerts@senger-solutions.com)
    hc-url            healthchecks.io ping URL

Rotate: `bao kv put secret/alerting/email recipient=… resend-password=… hc-url=…` then
`kubectl -n monitoring annotate externalsecret alertmanager-config force-sync=$(date +%s) --overwrite`.

## Bring-up (one-time, external)

1. Resend: verify `senger-solutions.com` as a sending domain; create an API key.
2. healthchecks.io: one check, **period 10m / grace 20m** (Alertmanager pings the Watchdog every 5m;
   these values ignore a single missed ping / an AM restart, alert on a dead cluster in ~30m).
3. Seed OpenBao (above), merge, let ArgoCD sync.

## Verify

    kubectl -n monitoring get externalsecret alertmanager-config        # SecretSynced=True
    kubectl -n monitoring get pods | grep -E 'vmalert|vmalertmanager'   # both 2/2 Running
    # rules loaded + Watchdog firing:
    kubectl -n monitoring port-forward svc/vmalert-vm-victoria-metrics-k8s-stack 18080:8080 &
    curl -s localhost:18080/api/v1/alerts | grep Watchdog
    # notifications succeeding (webhook = healthchecks, email = Resend):
    kubectl -n monitoring port-forward svc/vmalertmanager-vm-victoria-metrics-k8s-stack 19093:9093 &
    curl -s localhost:19093/metrics | grep alertmanager_notifications_.*_total.*'"webhook"\|"email"'

- healthchecks.io check should show **up**. A green Watchdog + a healthy check = the pipeline works.
- End-to-end email test: `kubectl -n monitoring apply` a throwaway VMRule with `expr: vector(1)`,
  `severity: warning` → email arrives (~30s); `kubectl delete` it → resolved email (~5m, next
  group_interval). Delete the test rule afterward.

## Silence / mute

    kubectl -n monitoring exec -it vmalertmanager-vm-victoria-metrics-k8s-stack-0 -c alertmanager -- \
      amtool --alertmanager.url=http://localhost:9093 silence add alertname=<name> -d 2h -c "maint"

## "No mail arriving" checklist

1. ExternalSecret `SecretSynced`? If not → OpenBao key missing / store unhealthy (openbao-recovery.md).
2. `alertmanager_notifications_failed_total{integration="email"}` > 0 → Resend key wrong or domain
   unverified; check AM logs (`-c alertmanager`).
3. Alert actually firing? `curl vmalert …/api/v1/alerts`. Nothing firing = nothing to send.
4. Watchdog is emailed to no one by design — it routes only to the webhook.

## Known gaps

- **CNPG base-backup success isn't alertable.** Backups use the Barman Cloud *plugin*, so the in-core
  `cnpg_collector_last_available_backup_timestamp` stays 0. WAL health is covered
  (`pg_wal_archive_status{value="ready"}`); verify base backups via backups-recovery.md.
- **Longhorn volume health not alerted.** `longhorn-backend:9500` is the manager gRPC/API port, not a
  Prometheus endpoint (a scrape hangs). Proper path: kube-state-metrics custom-resource-state on
  `volumes.longhorn.io` `.status.robustness`.

## Failure modes

- Node/cluster down → email path dies with it; healthchecks.io stops receiving pings and alerts you.
- vmalertmanager not-ready after deploy → `alertmanager-config` secret absent (OpenBao unseeded).
- AM retain PVC lost → silences/nflog reset; alerts still fire (acceptable).
- Noisy alert → tune `for:`/threshold in `vmrule-roastery.yaml`; silence via amtool meanwhile.

# Backups (Garage + Longhorn + CNPG) — bring-up & recovery

#7d. Off-NVMe backup target: **Garage** single-node S3 (`infrastructure/garage`, wave 13) on the
VM's HDD disk at `/var/mnt/garage` (kept off Longhorn so an NVMe failure doesn't also lose
backups). Consumers: **Longhorn** `RecurringJob daily-backup` (nightly volume snapshots) and
**CNPG** via the Barman Cloud Plugin (`cnpg-barman-plugin`, wave 14) — continuous WAL + daily base
backup for PITR. Proxmox/Talos disk attach + Garage init are operator-run; the rest reconciles.

## Bring-up

### 1. Attach HDD (Proxmox)
Attach a ~200 GB **HDD-backed** disk to `roastery-1` — must be the only rotational disk (Talos
`garage` userVolume selector `disk.rotational && !system_disk`; an SSD here gets picked instead).

### 2. Apply Talos config
    talhelper genconfig
    talosctl apply-config -e 192.168.178.16 -n 192.168.178.16 \
      --talosconfig clusterconfig/talosconfig -f clusterconfig/roastery-roastery-1.yaml
    # live node → needs the admin cert; do NOT use --insecure
Confirm the mount: `talosctl -n 192.168.178.16 get mountstatus | grep garage`.

### 3–4. Seed OpenBao (chicken/egg — Garage needs tokens to boot; the S3 key only exists after init)

**4a. Tokens → unblocks the pod** (else `CreateContainerConfigError`):

    kubectl -n openbao exec -it openbao-0 -- sh -c "BAO_TOKEN=<root> bao kv put secret/garage/tokens \
      rpc-secret=$(openssl rand -hex 32) admin-token=$(openssl rand -hex 32)"

Force ESO sync if impatient; pod → Running (un-initialized).

**4b. Init layout/bucket/key** (inside the pod; capture Key ID + Secret — shown once):

    NODE_ID=$(kubectl -n garage exec deploy/garage -- /garage status | awk '/HEALTHY NODES/{f=1;next} f&&NF{print $1;exit}')
    kubectl -n garage exec deploy/garage -- /garage layout assign -z dc1 -c 180G "$NODE_ID"
    kubectl -n garage exec deploy/garage -- /garage layout apply --version 1
    kubectl -n garage exec deploy/garage -- /garage bucket create backups
    kubectl -n garage exec deploy/garage -- /garage key create backup-key
    kubectl -n garage exec deploy/garage -- /garage bucket allow --read --write backups --key backup-key

**4c. S3 creds → unblocks Longhorn + CNPG:**

    kubectl -n openbao exec -it openbao-0 -- sh -c "BAO_TOKEN=<root> bao kv put secret/garage/s3 \
      access-key-id=<id> secret-access-key=<secret>"

ESO fans these into 3 Secrets: `garage-tokens` (garage), `garage-s3` (databases → CNPG
ObjectStore), `longhorn-backup-credential` (longhorn-system). `REGION=garage` is a literal in the
templates (Garage enforces `s3_region` in SigV4 → else `AuthorizationHeaderMalformed`). Verify all
three SecretSynced.

### 5. Auto pick-up (no further action)
- Longhorn applies `defaultBackupStore` (`s3://backups@garage/longhorn`); `daily-backup` RecurringJob
  (02:00, retain 7, group `default`) covers all volumes.
- CNPG WAL archiver plugin on `Cluster pg` → `s3://backups/pg`; `ScheduledBackup pg-daily` (03:00,
  staggered off Longhorn; 6-field cron), retention 7d.

## Verify
- Garage: `kubectl -n garage exec deploy/garage -- /garage status` (layout v1); mount usage grows.
- Longhorn: `kubectl -n longhorn-system get backuptarget` Available=True; trigger a manual backup →
  appears under `s3://backups@garage/longhorn`.
- CNPG: `kubectl -n databases get cluster pg -o jsonpath='{.status.conditions}'` shows
  ContinuousArchiving True; `kubectl -n databases get backup`.
- **PITR test** (the real proof): restore into a throwaway `Cluster` with
  `bootstrap.recovery.source` + `externalClusters[].plugin` (barman-cloud, `barmanObjectName:
  pg-backups`, `serverName: pg`), confirm the `authelia` role/data, then delete it. Check field
  shapes vs `kubectl explain cluster.spec.externalClusters.plugin` first.

## Failure modes
| Failure | Notes |
| --- | --- |
| Garage down | backups pause; consumers retry. Nothing reads Garage on the hot path. |
| S3 cred/region wrong | Longhorn backuptarget error / CNPG ContinuousArchiving False. Region must be `garage` both sides (else `AuthorizationHeaderMalformed`); endpoint `http://garage.garage.svc:3900`. |
| Barman plugin not in `cnpg-system` | never registers with the operator (hard CNPG-I requirement). |
| cert-manager down | plugin's serving certs fail → plugin not Ready. |
| HDD fails | all backups lost at once — accepted (guards logical loss + NVMe failure, not whole-host). |
| OpenBao sealed / eso broken | ExternalSecrets stop refreshing; see openbao-recovery.md. |

## Related
postgres-recovery.md, openbao-recovery.md, `docs/superpowers/specs/2026-09-14-backups-garage-design.md`.

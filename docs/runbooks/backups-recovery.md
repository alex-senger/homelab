# Backups (Garage + Longhorn + CNPG) — bring-up & recovery

Sub-project #7d. Off-host, on-HDD backup target for the single-node roastery cluster:
**Garage** (`infrastructure/garage`, app `garage`, wave 13) is a single-node S3-compatible
store on the VM's dedicated HDD-backed disk, mounted at `/var/mnt/garage` and kept off
Longhorn on purpose (an NVMe failure must not also take the backups). Two consumers write
to it: **Longhorn** (`infrastructure/longhorn`) takes daily volume snapshots of all volumes
via `RecurringJob daily-backup`, and **CloudNativePG** (`infrastructure/postgres`, via the
Barman Cloud Plugin, `infrastructure/cnpg-barman-plugin`, app `cnpg-barman-plugin`, wave 14)
continuously archives WAL and takes a daily base backup for point-in-time recovery (PITR).

Every step in this runbook that produces key material or touches raw storage is
**operator-run at the console** — no manifest performs Garage init, and Talos/Proxmox disk
attachment is out of GitOps entirely. Everything else (the Garage Deployment, the
ExternalSecrets, the Longhorn RecurringJob, the CNPG ObjectStore/ScheduledBackup) reconciles
itself once ArgoCD syncs the merged branch.

## Status: not yet bootstrapped

The manifests for #7d are merged, but as with OpenBao's own bring-up
([openbao-recovery.md](openbao-recovery.md)), **Garage has never been initialized.** It has
no HDD disk to mount until the Proxmox/Talos steps below run, and even once its pod is
Running it starts with an empty layout — no bucket, no key, nothing for Longhorn or CNPG to
write to. This is the checklist for the first operator to bring the whole chain up, in
order.

## Bring-up sequence

### 1. Proxmox: attach the HDD-backed disk

In the Proxmox UI (or `qm set`), attach a **~200 GB HDD-backed** virtual disk to the
`roastery-1` VM. It must be the VM's only *rotational* disk — Talos's `garage` userVolume
selector (`talos/talconfig.yaml`) matches on `disk.rotational && !system_disk`, so an SSD/NVMe
disk added here would be selected instead and silently break the "off Longhorn, off NVMe"
isolation the design relies on. Boot (or hot-add + rescan) so Talos can see it.

### 2. Merge the branch, regenerate and apply Talos config

    cd /Users/asg/workspaces/homelab
    git checkout main && git merge feat/backups-garage   # or merge the PR
    talhelper genconfig
    talosctl apply-config -n 192.168.178.16 -e 192.168.178.16 \
      --file clusterconfig/roastery-cluster-roastery-1.yaml

Talos provisions the new `garage` userVolume (`minSize: 180GiB`, `maxSize: 195GiB`,
`filesystem.type: xfs`) on the disk matched in step 1, mounting it at `/var/mnt/garage` (the
path `infrastructure/garage/garage-pv.yaml`'s local PV points at, pinned to node
`roastery-1`). Confirm the mount came up before relying on it:

    talosctl -n 192.168.178.16 get mountstatus | grep garage

A missing or absent line here means the disk wasn't seen as rotational (recheck step 1) or
apply-config hasn't landed yet — stop and fix this before continuing; every step after this
one assumes durable storage exists under the Garage pod.

### 3. Let ArgoCD deploy Garage (unstarted)

ArgoCD syncs the `garage` app (wave 13) once the merge reaches `main`. The Deployment comes
up but **cannot actually start** yet: its container needs `GARAGE_RPC_SECRET` and
`GARAGE_ADMIN_TOKEN` from a Secret named `garage-tokens`, which doesn't exist until the
`ExternalSecret` of the same name resolves against OpenBao — and OpenBao doesn't have
`secret/garage/tokens` yet. This is the same bootstrap order as OpenBao's own `eso`
authentication: expect `kubectl -n garage get pods` to show the `garage` Deployment's pod
(`garage-<hash>`) stuck in `CreateContainerConfigError` (missing secret keys) until step 4 below is
done. That's expected — do not troubleshoot it as a bug.

### 4. OpenBao: seed the two Garage paths (chicken/egg, in this order)

Garage needs its RPC/admin tokens to boot at all, and `garage-key create` output only exists
*after* Garage has booted and its layout has been initialized. So the two `bao kv put`s split
across the init:

**4a. Tokens first — unblocks the Garage pod:**

    RPC_SECRET=$(openssl rand -hex 32)
    ADMIN_TOKEN=$(openssl rand -hex 32)
    kubectl -n openbao exec -it openbao-0 -- sh -c \
      "BAO_TOKEN=<root-or-admin-token> bao kv put secret/garage/tokens \
        rpc-secret=$RPC_SECRET admin-token=$ADMIN_TOKEN"

ESO's `garage-tokens` `ExternalSecret` (ns `garage`) resolves within its `refreshInterval`
(1h — force a sooner sync with `kubectl -n garage annotate externalsecret garage-tokens
force-sync=$(date +%s) --overwrite` if you don't want to wait), the Secret materialises, and
the Garage pod restarts into a running, but still un-initialized, single-node cluster.

    kubectl -n garage get pods                # garage-<hash> Running
    kubectl -n garage exec deploy/garage -- /garage status
                                                # HEALTHY NODES table, one node, no layout yet

**4b. Init the Garage layout, bucket, and key (operator, inside the running pod):**

    NODE_ID=$(kubectl -n garage exec deploy/garage -- /garage status \
      | awk '/HEALTHY NODES/{f=1;next} f && NF {print $1; exit}')

    kubectl -n garage exec deploy/garage -- /garage layout assign -z dc1 -c 180G "$NODE_ID"
    kubectl -n garage exec deploy/garage -- /garage layout apply --version 1

    kubectl -n garage exec deploy/garage -- /garage bucket create backups
    kubectl -n garage exec deploy/garage -- /garage key create backup-key
                                                # capture Key ID + Secret key — shown once
    kubectl -n garage exec deploy/garage -- /garage bucket allow \
      --read --write backups --key backup-key

Confirm the layout took and the bucket is writable:

    kubectl -n garage exec deploy/garage -- /garage status        # layout version 1, no staged changes
    kubectl -n garage exec deploy/garage -- /garage bucket info backups

**4c. S3 credentials — unblocks Longhorn and CNPG:**

    kubectl -n openbao exec -it openbao-0 -- sh -c \
      "BAO_TOKEN=<root-or-admin-token> bao kv put secret/garage/s3 \
        access-key-id=<garage-key-id> secret-access-key=<garage-secret-key>"

ESO now fills three separate Secrets from these two OpenBao paths:

| OpenBao path | ExternalSecret (namespace) | Target Secret | Consumer |
|---|---|---|---|
| `secret/garage/tokens` | `garage-tokens` (`garage`) | `garage-tokens` | Garage container env (`GARAGE_RPC_SECRET`/`GARAGE_ADMIN_TOKEN`) |
| `secret/garage/s3` | `garage-s3` (`databases`) | `garage-s3` | CNPG `ObjectStore pg-backups` (`ACCESS_KEY_ID`/`ACCESS_SECRET_KEY`; `REGION` is added as a literal `garage` by the ExternalSecret template — not from OpenBao — because Garage enforces its `s3_region` in the SigV4 signature) |
| `secret/garage/s3` | `longhorn-backup-credential` (`longhorn-system`) | `longhorn-backup-credential` | Longhorn backup target (templated to `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/`AWS_ENDPOINTS`) |

Check all three resolve:

    kubectl -n garage get externalsecret garage-tokens
    kubectl -n databases get externalsecret garage-s3
    kubectl -n longhorn-system get externalsecret longhorn-backup-credential
                                                # all SecretSynced / Ready=True

### 5. Longhorn and CNPG pick up the target automatically

No further operator action is needed once the three Secrets above exist:

- **Longhorn** reads `defaultBackupStore` (`backupTarget: s3://backups@garage/longhorn`,
  `backupTargetCredentialSecret: longhorn-backup-credential`) from its chart values and
  applies it as the cluster's backup target on the next reconcile. The `daily-backup`
  `RecurringJob` (`longhorn-system`, group `default`, `cron: "0 2 * * *"`, `retain: 7`) then
  runs nightly against **every** Longhorn volume in the `default` group — this deliberately
  includes the pre-existing OpenBao raft volume and the CNPG PVC (redundant with Barman PITR
  for Postgres, but harmless).
- **CNPG**, via the Barman Cloud Plugin (chart `plugin-barman-cloud` 0.8.0, app
  `cnpg-barman-plugin`, wave 14, namespace `cnpg-system` — same namespace as the CNPG
  operator, and it needs cert-manager live), starts WAL archiving against `ObjectStore
  pg-backups` (`s3://backups/pg`, endpoint `http://garage.garage.svc:3900`) as soon as
  `Cluster pg`'s `.spec.plugins` entry (`isWALArchiver: true`) reconciles. The `pg-daily`
  `ScheduledBackup` (`databases`, `method: plugin`, cron `0 0 3 * * *` = daily 03:00) takes
  the first base backup on its own schedule; `ObjectStore.spec.retentionPolicy: "7d"` prunes
  older WAL/base backups. CNPG's base backup runs at **03:00**, deliberately one hour after
  the Longhorn RecurringJob (02:00), so the two heavy backup workloads don't contend for the
  single HDD-backed Garage. Note: CNPG's `ScheduledBackup.spec.schedule` uses a **6-field** cron format with a leading **seconds** field (robfig/cron), not the 5-field Kubernetes CronJob format — so `0 0 3 * * *` means second 0, minute 0, hour 3, which is **daily at 03:00**.

## Acceptance / verification

- **Garage:** pod Running, layout applied, data landing on the HDD mount.

      kubectl -n garage get pods
      kubectl -n garage exec deploy/garage -- /garage status
      talosctl -n 192.168.178.16 get mountstatus | grep garage   # or df -h on the mount; usage grows after backups run

  S3 reachable in-cluster (from any pod, or a throwaway one):

      kubectl -n garage run s3-check --rm -it --restart=Never --image=amazon/aws-cli \
        --env=AWS_ACCESS_KEY_ID=<key-id> --env=AWS_SECRET_ACCESS_KEY=<secret> \
        -- --endpoint-url http://garage.garage.svc:3900 s3 ls s3://backups

- **Longhorn:** backup target healthy, and a manual backup actually lands in Garage.

      kubectl -n longhorn-system get backuptarget
                                                  # (or the Longhorn UI's Backup page) — Available=True, no error message

  Trigger one manually rather than waiting for 02:00 (Longhorn UI: Volume → Create Backup, or
  `kubectl -n longhorn-system create -f -` a one-off `Backup` CR against an existing volume's
  `Engine`), then confirm it shows up both in `kubectl -n longhorn-system get backup` and as
  objects under `s3://backups@garage/longhorn` in Garage.

- **CNPG:** WAL archiving running and a completed base backup.

      kubectl -n databases get cluster pg -o jsonpath='{.status.conditions}' | jq .
                                                  # look for a "ContinuousArchiving" condition True
      kubectl -n databases get backup
                                                  # pg-daily-<ts> phase: completed (after 03:00, or trigger one early — see below)

  Force an out-of-schedule base backup to verify sooner:

      kubectl -n databases create -f - <<'EOF'
      apiVersion: postgresql.cnpg.io/v1
      kind: Backup
      metadata:
        name: pg-manual-verify
        namespace: databases
      spec:
        cluster:
          name: pg
        method: plugin
        pluginConfiguration:
          name: barman-cloud.cloudnative-pg.io
      EOF
      kubectl -n databases get backup pg-manual-verify -w

  **Test a PITR restore into a throwaway Cluster** — the actual proof the backup chain works,
  not just that objects exist in Garage:

      kubectl -n databases apply -f - <<'EOF'
      apiVersion: postgresql.cnpg.io/v1
      kind: Cluster
      metadata:
        name: pg-restore-test
        namespace: databases
      spec:
        instances: 1
        imageName: ghcr.io/cloudnative-pg/postgresql:17
        storage:
          size: 5Gi
          storageClass: longhorn-retain
        bootstrap:
          recovery:
            source: pg-origin
        externalClusters:
          - name: pg-origin
            plugin:
              name: barman-cloud.cloudnative-pg.io
              parameters:
                barmanObjectName: pg-backups
                serverName: pg
      EOF
      kubectl -n databases get cluster pg-restore-test -w
                                                  # Cluster in healthy state, 1/1 ready — recovered from Garage

  Confirm the `authelia` role and its data actually made it across (adapt the connection
  check from [postgres-recovery.md](postgres-recovery.md)'s acceptance section, pointed at
  `pg-restore-test-rw.databases.svc`), then delete the throwaway cluster — it exists only to
  prove restore works, not to run anything:

      kubectl -n databases delete cluster pg-restore-test

  Confirm field names/shape (`bootstrap.recovery.source`, `externalClusters[].plugin`)
  against the CRD actually installed (`kubectl explain cluster.spec.externalClusters.plugin`)
  before relying on this in a real incident — the Barman Cloud Plugin's recovery contract is
  newer and more likely to have shifted between chart versions than the rest of this runbook.

## Known follow-up (not fixed in #7d)

The `postgres` ArgoCD Application shows **`OutOfSync`** on the `Cluster pg` resource after
this sub-project. The CNPG operator defaults roughly twenty top-level `Cluster.spec` fields
(and some nested ones) at admission/reconcile time that were never set in
`infrastructure/postgres/cluster.yaml`, so ArgoCD's live diff always finds drift against the
committed manifest. This is benign — the database itself is healthy, and ArgoCD's `selfHeal`
just no-ops a few minutes' worth of churn each sync, it does not fight the operator or revert
real state. It was deliberately **not** fixed here: a guessed `ignoreDifferences` field list
is brittle (breaks silently on the next CNPG version bump) and `managedFields`-based ignoring
isn't usable (the fields aren't attributed to a distinct field manager). The correct fix is a
narrowly-scoped `ignoreDifferences` on `cluster/applications/postgres.yaml`, diagnosed from
ArgoCD's actual live diff (`argocd app diff postgres`, or the UI's Diff tab) rather than
guessed from the CRD — track this as a deferred follow-up.

## Failure modes

| Failure | Effect | Notes |
|---|---|---|
| Garage down (pod crash, node reboot) | Backups pause; Longhorn/CNPG retry and catch up once it's back | Live workloads are unaffected — Garage is a backup target only, nothing reads from it on the hot path |
| S3 endpoint or credential misconfigured | Longhorn `backuptarget` shows an error; CNPG `ContinuousArchiving` condition goes False / `Backup` objects fail | Check, in order: the `garage-s3` Secret actually has `ACCESS_KEY_ID`/`ACCESS_SECRET_KEY`/`REGION` (CNPG) or `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/`AWS_ENDPOINTS` (Longhorn) populated; that the region matches Garage's `s3_region` (`garage`) on both sides — a mismatch fails with `AuthorizationHeaderMalformed`; the bucket policy (`garage bucket info backups` shows `backup-key` with read+write); the endpoint URL matches `http://garage.garage.svc:3900` exactly (no trailing slash, correct scheme) |
| Barman Cloud Plugin not in `cnpg-system` | Plugin never registers with the CNPG operator; `isWALArchiver` plugin config silently fails to attach | The plugin **must** run in the same namespace as the CNPG operator (`cnpg-system`) — this is a hard CNPG-I requirement, not a preference |
| cert-manager down or missing | Barman Cloud Plugin fails to start (its webhook/gRPC serving certs come from cert-manager) | Plugin depends on cert-manager being live at wave 14; check `kubectl -n cert-manager get pods` first if the plugin pod isn't Ready |
| HDD physically fails | All backups on it are lost — Garage data, Longhorn snapshots, CNPG WAL/base backups, all at once | On-host limitation, accepted by design for #7d: this protects against *logical* loss (bad deploy, accidental delete, NVMe failure) and operator error, not against losing the whole VM/host. Off-host replication is out of scope here |
| OpenBao sealed or `eso` role broken | The three `ExternalSecret`s stop refreshing; existing Secrets keep working (ESO doesn't delete on failed refresh) until they're needed fresh (e.g. after a Garage restart with no cached `garage-tokens`) | See [openbao-recovery.md](openbao-recovery.md) for OpenBao's own recovery |

## Related

- [postgres-recovery.md](postgres-recovery.md) — CNPG operator/cluster bring-up this
  sub-project builds on.
- [openbao-recovery.md](openbao-recovery.md) — OpenBao init/unseal; the source of both
  `secret/garage/tokens` and `secret/garage/s3`.
- `docs/superpowers/specs/2026-09-14-backups-garage-design.md` — design rationale (why
  on-host HDD, why Garage over MinIO, retention choices).

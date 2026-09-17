# Nextcloud migration & recovery (#7e)

Migrated the old Nextcloud **AIO** (v33.0.8, Postgres, ~109 GB) onto the in-cluster Nextcloud,
keeping all files; `asg` → Authelia OIDC, `admin` local break-glass. Records the procedure +
the gotchas actually hit (the mac is remote over WireGuard, so all bulk transfers run in-cluster).

## Prereqs
- In-cluster image pinned to the AIO version (`image.tag: 33.0.8-apache`); re-check
  `docker exec --user www-data nextcloud-aio-nextcloud php occ status` before cutover.
- Longhorn free capacity ≥ ~160 Gi.

## 1. Seed OpenBao + merge
    bao kv put secret/databases/nextcloud password=<rand>
    bao kv put secret/nextcloud/app admin-password=<rand>
    bao kv put secret/nextcloud/oidc client-secret=<plain> client-secret-hash=@/tmp/hash  # argon2, strip "Digest: "
Merge the PR. Two first-boot gotchas: the CNPG `nextcloud` role can race its secret (nudge:
`kubectl -n databases annotate cluster pg reconcile-trigger=$(date +%s) --overwrite`), and the
image entrypoint won't re-run the installer after a failed first boot → run it manually with the
pod's own env:
    kubectl -n nextcloud exec deploy/nextcloud -c nextcloud -- bash -c \
      'php occ maintenance:install --database pgsql --database-host "$POSTGRES_HOST" \
       --database-name "$POSTGRES_DB" --database-user "$POSTGRES_USER" --database-pass "$POSTGRES_PASSWORD" \
       --admin-user "$NEXTCLOUD_ADMIN_USER" --admin-pass "$NEXTCLOUD_ADMIN_PASSWORD" --data-dir /var/www/html/data'

## 2. Extract from AIO
    docker exec nextcloud-aio-database pg_dump -U nextcloud nextcloud_database > nc.sql   # owner role in the dump is oc_nextcloud
    # note the appdata_<instanceid> dir name and old config.php instanceid — needed in §4

## 3. Transfer in-cluster (mac is remote/WG-flaky → never stream through it)
Stand up a temporary rsync-daemon loader pod mounting the data PVC on a LAN LoadBalancer IP; the
old server rsyncs the 109 GB straight to it over the LAN (resumable). NOTE the chart mounts the
datadir via `subPath: data`, so the datadir is the PVC's `data/` subdir. Import the DB detached
inside a pod (not streamed over the mac): drop the fresh schema, **pre-create role `oc_nextcloud`**
(the dump's owner) so restore succeeds, restore, then `REASSIGN OWNED BY oc_nextcloud TO nextcloud`
and drop the role.

## 4. Reconcile + repair
    kubectl -n nextcloud exec deploy/nextcloud -c nextcloud -- php occ config:system:set instanceid --value=<old>
    # encryption is OFF here → old secret/passwordsalt don't matter; reset admin instead of matching passwordsalt:
    kubectl -n nextcloud exec deploy/nextcloud -c nextcloud -- php occ user:resetpassword admin
    kubectl -n nextcloud exec deploy/nextcloud -c nextcloud -- php occ maintenance:data-fingerprint
    kubectl -n nextcloud exec deploy/nextcloud -c nextcloud -- php occ files:scan --all

## 5. OIDC mapping
The old instance used **PocketID**, so its accounts are hashed `user_oidc` uids and the real data
sat under one of them (not `asg`). To land Authelia's `asg` on a clean account: `occ app:install
user_oidc` (**install**, not just enable — the AIO DB flag came over but the files didn't), then move
the data to a fresh local `asg` (filesystem `mv` of the hashed user's `files/` → `asg/files/` +
`occ files:scan asg` — `files:transfer-ownership` fails the same-disk free-space check). Configure:
    occ user_oidc:provider Authelia --clientid=nextcloud --clientsecret=<plain> \
      --discoveryuri=https://auth.senger-solutions.com/.well-known/openid-configuration \
      --mapping-uid=preferred_username --unique-uid=0
    occ config:app:set user_oidc allow_multiple_user_backends --value=1   # bind OIDC to the existing local asg
Delete the old PocketID provider + hashed accounts once verified.

## 6. Expose + backups
Add `nextcloud.senger-solutions.com` to the VPS nginx SNI relay (public); CoreDNS already resolves
it in-cluster (→ `.3`). No backup action: the `nextcloud` DB is covered by CNPG `pg-daily` and the
data PVC by the Longhorn `daily-backup` RecurringJob.

## Failure modes
- OIDC "Failed to provision user" = uid mismatch — see [authelia-recovery.md](authelia-recovery.md).
- 400 "trusted domain" → add the host to `trusted_domains` (values `proxy.config.php`).
- Redirect loop / wrong scheme → `overwriteprotocol`/`overwritehost`/`trusted_proxies`.
- Files present but previews fail → `instanceid` mismatch (§4) or missing `files:scan`.

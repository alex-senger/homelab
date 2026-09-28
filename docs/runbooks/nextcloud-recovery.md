# Nextcloud recovery

`infrastructure/nextcloud` (app `nextcloud`), DB `nextcloud` on the shared CNPG `Cluster pg`.
`asg` logs in via Authelia OIDC; `admin` is local break-glass. Encryption is OFF. Backups: DB via
CNPG `pg-daily`, data PVC via the Longhorn `daily-backup` RecurringJob.

## First boot
- CNPG `nextcloud` role can race its secret → nudge:
  `kubectl -n databases annotate cluster pg reconcile-trigger=$(date +%s) --overwrite`
- The entrypoint won't re-run the installer after a failed first boot → run it with the pod's env:

      kubectl -n nextcloud exec deploy/nextcloud -c nextcloud -- bash -c \
        'php occ maintenance:install --database pgsql --database-host "$POSTGRES_HOST" \
         --database-name "$POSTGRES_DB" --database-user "$POSTGRES_USER" --database-pass "$POSTGRES_PASSWORD" \
         --admin-user "$NEXTCLOUD_ADMIN_USER" --admin-pass "$NEXTCLOUD_ADMIN_PASSWORD" --data-dir /var/www/html/data'

- The chart mounts the datadir via `subPath: data` → it's the PVC's `data/` subdir.

## OIDC (`user_oidc`)
Install the app (`occ app:install user_oidc`), then:

    occ user_oidc:provider Authelia --clientid=nextcloud --clientsecret=<plain> \
      --discoveryuri=https://auth.senger-solutions.com/.well-known/openid-configuration \
      --mapping-uid=preferred_username --unique-uid=0
    occ config:app:set user_oidc allow_multiple_user_backends --value=1   # bind OIDC to the local asg

## Repair after restoring data
    kubectl -n nextcloud exec deploy/nextcloud -c nextcloud -- php occ maintenance:data-fingerprint
    kubectl -n nextcloud exec deploy/nextcloud -c nextcloud -- php occ files:scan --all

`files:transfer-ownership` fails the same-disk free-space check; move a user's `files/` on disk and
`occ files:scan <user>` instead.

## Failure modes
- OIDC "Failed to provision user" = uid mismatch: check `--mapping-uid=preferred_username` and
  `allow_multiple_user_backends` (above). Client-side errors: [authelia-recovery.md](authelia-recovery.md).
- 400 "trusted domain" → add the host to `trusted_domains` (values `proxy.config.php`).
- Redirect loop / wrong scheme → `overwriteprotocol`/`overwritehost`/`trusted_proxies`.
- Files present but previews fail → `instanceid` mismatch or missing `files:scan`.

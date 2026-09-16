# Nextcloud migration & recovery (#7e)

Migrate the old Nextcloud **AIO** instance (v33.0.8, PostgreSQL, ~109 GB data)
onto the in-cluster Nextcloud, keeping all files and both users. `asg` → Authelia
OIDC; `admin` stays local break-glass.

## 0. Prerequisites
- In-cluster Nextcloud image pinned to the AIO version (`33.0.8-apache`). If AIO
  auto-updated, re-check `docker exec --user www-data nextcloud-aio-nextcloud php occ status`
  and bump `infrastructure/nextcloud/values.yaml` `image.tag` to match before cutover.
- Verify Longhorn free capacity ≥ ~160 Gi before restoring data:
  `kubectl -n longhorn-system get nodes.longhorn.io -o wide` (schedulable storage).

## 1. Seed OpenBao
Generate the OIDC client secret + its argon2 hash, an admin password, and copy the
identity keys out of the OLD config.php so the restored DB/data stay valid.

```sh
# On a box with the authelia CLI (for the argon2 hash):
CLIENT_SECRET=$(openssl rand -hex 32)
HASH=$(docker run --rm authelia/authelia:4.39.26 \
  authelia crypto hash generate argon2 --password "$CLIENT_SECRET" --no-confirm \
  | sed -n 's/^Digest: //p')
ADMIN_PW=$(openssl rand -base64 24)

# From the OLD instance's config.php (docker exec ... cat .../config/config.php):
#   instanceid, secret, passwordsalt
bao kv put secret/nextcloud/oidc client-secret="$CLIENT_SECRET" client-secret-hash=@<(printf %s "$HASH")
bao kv put secret/nextcloud/app admin-password="$ADMIN_PW"
bao kv put secret/databases/nextcloud password="$(openssl rand -base64 24)"
bao kv put secret/nextcloud/identity instanceid=<old> secret=<old> passwordsalt=<old>
```
Never inline `$`-containing hashes in `sh -c`; always `=@file` (learned in #7b).

## 2. Merge & let ArgoCD install fresh
Merge the PR. ArgoCD creates the `nextcloud` DB/role (wave 21 app already synced),
the Authelia OIDC client (wave 22), and Nextcloud (wave 23), which runs a FRESH
`occ maintenance:install` against the empty `nextcloud` DB. Wait until the app is
Healthy and the web UI loads (fresh, empty).

## 3. Extract from the old AIO host
```sh
# Stop AIO (AIO web UI → Stop containers), then start only the database container.
docker exec nextcloud-aio-database pg_dump -U oc_nextcloud nextcloud_database > nextcloud-db.sql
# (Confirm DB/user names: docker exec nextcloud-aio-database env | grep POSTGRES)
tar -C /var/lib/docker/volumes/nextcloud_aio_nextcloud_data/_data -czf nextcloud-data.tgz .
docker exec nextcloud-aio-nextcloud cat /var/www/html/config/config.php > old-config.php
```

## 4. Load the DB into CNPG
Put Nextcloud in maintenance mode, drop the fresh schema, restore the AIO dump.
```sh
NC=$(kubectl -n nextcloud get pod -l app.kubernetes.io/name=nextcloud -o name | head -1)
kubectl -n nextcloud exec "$NC" -- php occ maintenance:mode --on
# Drop+recreate the public schema in the nextcloud DB, then restore:
kubectl -n databases exec pg-1 -- psql -U nextcloud -d nextcloud \
  -c 'DROP SCHEMA public CASCADE; CREATE SCHEMA public;'
cat nextcloud-db.sql | kubectl -n databases exec -i pg-1 -- psql -U nextcloud -d nextcloud
```
NOTE: restoring the AIO DB replaces the users table, so `admin`/`asg` now use their
OLD AIO passwords — the ESO-seeded admin password only applied to the fresh install.
Use the old admin password for break-glass; reset later via `occ user:resetpassword`.

## 5. Restore the 109 GB data directory
Stream the tarball into the data PVC via the running pod (avoids one huge `kubectl cp`).
```sh
kubectl -n nextcloud exec -i "$NC" -- tar -C /var/www/html/data -xzf - < nextcloud-data.tgz
kubectl -n nextcloud exec "$NC" -- chown -R www-data:www-data /var/www/html/data
```

## 6. Reconcile identity + config, then repair
The restored DB/data reference the OLD `instanceid`/`secret`/`passwordsalt` — set them
so encryption and file references keep working:
```sh
kubectl -n nextcloud exec "$NC" -- php occ config:system:set instanceid --value=<old>
kubectl -n nextcloud exec "$NC" -- php occ config:system:set secret --value=<old>
kubectl -n nextcloud exec "$NC" -- php occ config:system:set passwordsalt --value=<old>
kubectl -n nextcloud exec "$NC" -- php occ maintenance:data-fingerprint
kubectl -n nextcloud exec "$NC" -- php occ files:scan --all
kubectl -n nextcloud exec "$NC" -- php occ files:scan-app-data
kubectl -n nextcloud exec "$NC" -- php occ maintenance:repair --include-expensive
kubectl -n nextcloud exec "$NC" -- php occ maintenance:mode --off
```
Verify: log in as `admin` (local) and `asg` (local password from the old DB) — files present.

## 7. Wire OIDC (asg → Authelia), test on a throwaway user FIRST
```sh
kubectl -n nextcloud exec "$NC" -- php occ app:install user_oidc
kubectl -n nextcloud exec "$NC" -- php occ user_oidc:provider Authelia \
  --clientid=nextcloud --clientsecret="$CLIENT_SECRET" \
  --discoveryuri=https://auth.senger-solutions.com/.well-known/openid-configuration \
  --mapping-uid=preferred_username --mapping-display-name=name --mapping-email=email \
  --unique-uid=0
```
- **Spike A — verify the flag:** run `occ user_oidc:provider --help` and confirm the
  exact unique-uid flag name (README calls it `--unique-uid`; verify on 33.0.8). The
  goal: Nextcloud uid == raw `preferred_username`, no provider prefix.
- **Spike B — throwaway test:** create local user `testmap` + a matching Authelia user
  with `preferred_username: testmap`, log in via OIDC, confirm it binds to the existing
  account (keeps a test file) — NOT a new prefixed user. Only then rely on it for `asg`.
- Ensure Authelia emits `preferred_username: asg` (check the ID token / userinfo).
- Keep the local login option visible so `admin` break-glass still works
  (`occ config:app:set user_oidc allow_multiple_user_backends --value=1` if needed;
  do NOT set auto-redirect / single-logout that hides local login initially).
- **Fallback:** if `user_oidc` won't bind cleanly, switch to `oidc_login` (pulsejet),
  which maps by username directly — re-scope with the owner first.

## 8. Operator DNS/relay
Add `nextcloud.senger-solutions.com` to the VPS nginx SNI relay (public) and to LAN
DNS/hosts → Gateway `.3`, like `auth.`/`whoami.`.

## 9. Backups
No action: the `nextcloud` DB is covered by the existing CNPG `ScheduledBackup pg-daily`
(→ Garage), and the data PVC by the Longhorn `daily-backup` RecurringJob. Confirm a WAL
push and a PVC snapshot after cutover.

## Failure modes
- "Failed to provision user" on OIDC = uid mismatch (Spike A/B). Fix the mapping/claim.
- 400 "trusted domain" = add the host to `trusted_domains` (values proxy.config.php).
- Redirect loop / wrong scheme = `overwriteprotocol`/`overwritehost`/`trusted_proxies`.
- Files show but previews/downloads fail = `instanceid`/`secret`/`passwordsalt` mismatch
  (step 6) or missing `files:scan`.

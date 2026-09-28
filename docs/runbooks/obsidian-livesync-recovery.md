# Obsidian LiveSync (CouchDB) — setup & recovery

`apps/obsidian-livesync/`, app `obsidian-livesync`, wave 24. Single-node CouchDB behind the
`external` Gateway at `https://obsidian.senger-solutions.com`. Vaults are end-to-end encrypted
(content and paths) by the plugin; the server and its backups hold ciphertext only.

Layers: Cloudflare WAF secret header → Cloudflare rate limit → CouchDB auth (anonymous only
`/_up`) → non-admin `livesync` user scoped to vault DBs. The admin account never leaves OpenBao.

## First-time setup

1. **Server secrets** (random values):

       kubectl -n openbao exec -it openbao-0 -- sh -c 'BAO_TOKEN=<root> bao kv put secret/obsidian-livesync/couchdb \
         username=<admin-user> password=<random ≥32> erlang-cookie=<random 32 hex>'

   ESO syncs `couchdb-admin` within 1h; force it with
   `kubectl -n obsidian-livesync annotate externalsecret couchdb-admin force-sync=$(date +%s) --overwrite`.
2. **DNS:** Cloudflare record `obsidian` → VPS, **proxied**. The VPS only accepts 443 from Cloudflare.
3. **WAF secret header:** Cloudflare → Security → WAF → Custom rules → Block when

       (http.host eq "obsidian.senger-solutions.com" and http.request.method ne "OPTIONS"
        and not any(http.request.headers["x-livesync-key"][*] eq "<random ≥32>"))

   `OPTIONS` is exempt because CORS preflights can't carry custom headers. Keep the value in the
   password manager.
4. **Rate limit:** Cloudflare → Security → WAF → Rate limiting rules, `http.host eq
   "obsidian.senger-solutions.com"`, 100 requests / 10 s per IP → Block 1 min.
5. **VPS relay:** `cd vps && ansible-playbook playbook.yml -K --tags nginx`.
6. **Vault user** (admin, once). Admin creds from the cluster, header from the password manager:

       H=https://obsidian.senger-solutions.com; K='X-LiveSync-Key: <header value>'
       A="$(kubectl -n obsidian-livesync get secret couchdb-admin -o jsonpath='{.data.username}' | base64 -d):$(kubectl -n obsidian-livesync get secret couchdb-admin -o jsonpath='{.data.password}' | base64 -d)"
       curl -s -u "$A" -H "$K" -X PUT "$H/_users/org.couchdb.user:livesync" -H 'Content-Type: application/json' \
         -d '{"name":"livesync","password":"<random ≥32>","roles":[],"type":"user"}'

## Per vault

Non-admins can't create databases, so the admin creates each one and scopes it to `livesync`:

    curl -s -u "$A" -H "$K" -X PUT "$H/<vault-db>"
    curl -s -u "$A" -H "$K" -X PUT "$H/<vault-db>/_security" -H 'Content-Type: application/json' \
      -d '{"members":{"names":["livesync"],"roles":[]},"admins":{"names":[],"roles":[]}}'

Database names: lowercase, e.g. `vault-notes`.

## Plugin setup (per device)

Self-hosted LiveSync → Remote: CouchDB:
- URI `https://obsidian.senger-solutions.com`, username `livesync`, its password, database `<vault-db>`.
- **Custom Headers:** `X-LiveSync-Key: <header value>`.
- **End-to-end encryption** on, passphrase in the password manager, **Path Obfuscation** on.
- Skip "Check server requirements" fixes. They need admin, and the config is already in `livesync.ini`.

Lose the passphrase and the server copy is unreadable; rebuild from a device instead.

## Restore

- **From backup:** Longhorn UI → Backup → the `couchdb-data` volume → restore to a new volume. Disable
  selfHeal on the ArgoCD app, scale `couchdb` to 0, point the PVC at the restored volume, scale back
  up, re-enable selfHeal. See `backups-recovery.md` for the backup target.
- **From a device ("Rebuild remote"):** the plugin deletes and recreates the database, which
  `livesync` can't do. Recreate it and its `_security` as admin ("Per vault"), then run the rebuild.

## Rotation

- **Admin password / Erlang cookie:** `bao kv put` (setup step 1), force-sync the ExternalSecret,
  `kubectl -n obsidian-livesync rollout restart deploy/couchdb` (env vars don't reload).
- **`livesync` password:** as admin, GET `$H/_users/org.couchdb.user:livesync`, PUT it back with
  the same `_rev` and a new `"password"`. Update each device.
- **WAF header value:** edit the Cloudflare rule, update each device's Custom Headers.

## Gotchas

- Changing `uuid` in `livesync.ini` resets every device's replication checkpoint. Don't.
- Every external `curl` needs `-H "$K"`; without it Cloudflare returns 403 before CouchDB sees it.
- Host unreachable but pod healthy → SNI allowlist or unproxied DNS; see `external-reach-recovery.md`.

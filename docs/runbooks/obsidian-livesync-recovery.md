# Obsidian LiveSync (CouchDB) — setup & recovery

`apps/obsidian-livesync/`, app `obsidian-livesync`, wave 24. Single-node CouchDB behind the
`external` Gateway at `https://obsidian.senger-solutions.com`. Vaults are end-to-end encrypted
(content and paths) by the plugin; the server and its backups hold ciphertext only.

Layers: Cloudflare WAF secret header → Cloudflare rate limit → CouchDB auth (anonymous only
`/_up`) → non-admin `livesync` user scoped to vault DBs. Devices never hold the admin account, and
admin calls never cross the public path (see "Admin access").

## Admin access

Always through a port-forward (enters the pod directly: no Cloudflare, no WAF header, no CNP). The
netrc via process substitution keeps the password out of `ps`:

    kubectl -n obsidian-livesync port-forward deploy/couchdb 5984:5984 &
    H=http://localhost:5984
    AU=$(kubectl -n obsidian-livesync get secret couchdb-admin -o jsonpath='{.data.username}' | base64 -d)
    AP=$(kubectl -n obsidian-livesync get secret couchdb-admin -o jsonpath='{.data.password}' | base64 -d)
    adm() { curl -s --netrc-file <(printf 'machine localhost login %s password %s\n' "$AU" "$AP") "$@"; }

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
   "obsidian.senger-solutions.com"`, 300 requests / 10 s per IP → Block (Free plan: 10 s period and
   10 s block are the only options). A backstop behind the header rule, so kept generous: a first
   full sync of a large vault fetches many chunks. If sync stalls with 429s, raise it.
5. **VPS relay:** `cd vps && ansible-playbook playbook.yml -K --tags nginx`.
6. **Vault user** (once, via "Admin access"):

       adm -X PUT "$H/_users/org.couchdb.user:livesync" -H 'Content-Type: application/json' \
         -d '{"name":"livesync","password":"<random ≥32>","roles":[],"type":"user"}'
7. **Check** from outside the LAN: `curl -s -o /dev/null -w '%{http_code}\n'
   https://obsidian.senger-solutions.com/_up` → 403 (no header); with
   `-H 'X-LiveSync-Key: <value>'` → 200.

## Per vault

Non-admins can't create databases, so the admin creates each one and scopes it to `livesync`
(via "Admin access"):

    adm -X PUT "$H/<vault-db>"
    adm -X PUT "$H/<vault-db>/_security" -H 'Content-Type: application/json' \
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

- **From backup** (a bound PVC's volume can't be swapped, so the PVC is recreated):
  1. Longhorn UI → Backup → the `couchdb-data` volume's backup → Restore, to a new volume.
  2. Disable selfHeal on the ArgoCD app `obsidian-livesync`, then
     `kubectl -n obsidian-livesync scale deploy/couchdb --replicas=0`.
  3. `kubectl -n obsidian-livesync delete pvc couchdb-data`. The old PV stays (`longhorn-retain`).
  4. Longhorn UI → the restored volume → Create PV/PVC, PVC name `couchdb-data`, namespace
     `obsidian-livesync`.
  5. Scale back to 1, re-enable selfHeal. Delete the old volume in Longhorn once the data checks out.

  See `backups-recovery.md` for the backup target.
- **From a device ("Rebuild remote"):** the plugin deletes and recreates the database, which
  `livesync` can't do. Reset it as admin first (via "Admin access"):

      adm -X DELETE "$H/<vault-db>"
      adm -X PUT "$H/<vault-db>"
      adm -X PUT "$H/<vault-db>/_security" -H 'Content-Type: application/json' \
        -d '{"members":{"names":["livesync"],"roles":[]},"admins":{"names":[],"roles":[]}}'

  Then run the rebuild on the device. Its own delete of the remote may report an error; the
  database is already empty, so the re-upload proceeds.

## Rotation

- **Admin password / Erlang cookie:** `bao kv put` (setup step 1), force-sync the ExternalSecret,
  `kubectl -n obsidian-livesync rollout restart deploy/couchdb` (env vars don't reload).
- **`livesync` password:** via "Admin access", `adm "$H/_users/org.couchdb.user:livesync"`, PUT it
  back with the same `_rev` and a new `"password"`. Update each device.
- **WAF header value:** edit the Cloudflare rule, update each device's Custom Headers.

## Gotchas

- Changing `uuid` in `livesync.ini` resets every device's replication checkpoint. Don't.
- Every external request needs the `X-LiveSync-Key` header; without it Cloudflare returns 403
  before CouchDB sees it. Admin work uses the port-forward instead.
- Host unreachable but pod healthy → SNI allowlist or unproxied DNS; see `external-reach-recovery.md`.

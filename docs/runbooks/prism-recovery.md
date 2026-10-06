# Prism (read-only file share) — setup & recovery

`apps/prism/`, app `prism`, wave 24. nginx serving the `prism-files` PVC read-only as a directory
listing at `https://prism.senger-solutions.com`, behind one basic-auth user. No uploads, no app
state; the PVC holds a copy of a Nextcloud folder.

Layers: Cloudflare rate limit → nginx global limit (10 r/s, burst 20) → basic auth → GET/HEAD
only on a read-only mount, symlinks refused, `no-store`. Apps see the tunnel IP, so per-IP limiting
only works at Cloudflare; the nginx cap is shared by all clients.

## First-time setup

1. **Password** (prompts; macOS LibreSSL lacks `-6`, hence nix's OpenSSL):

       printf 'prism:%s\n' "$(nix run nixpkgs#openssl -- passwd -6)" | kubectl -n openbao exec -i openbao-0 -- \
         sh -c 'BAO_TOKEN=<root> bao kv put secret/prism/auth htpasswd=-'

   Force the sync with
   `kubectl -n prism annotate externalsecret prism-htpasswd force-sync=$(date +%s) --overwrite`.
2. **DNS:** Cloudflare record `prism` → VPS, **proxied** (replace any old redirect to Nextcloud).
3. **Rate limit:** Cloudflare → Security → WAF → Rate limiting rules, `http.host eq
   "prism.senger-solutions.com"`, 60 requests / 10 s per IP → Block.
4. **VPS relay:** `cd vps && ansible-playbook playbook.yml -K --tags nginx`.
5. **Files:** see below.
6. **Check** from outside the LAN: `curl -s -o /dev/null -w '%{http_code}\n'
   https://prism.senger-solutions.com/` → 401; with `-u prism` → 200.

## Load / refresh files

nginx mounts the PVC read-only, so write through a throwaway loader pod (RWO allows both pods on
the single node). Go via a local copy with `kubectl cp --retries`: piping `tar` between two
`kubectl exec` streams truncates the tail when the API connection drops (`tar: short read`).

    kubectl -n prism run loader --image=docker.io/busybox:1.37 --restart=Never \
      --override-type=strategic --overrides='{"spec":{"securityContext":{"runAsNonRoot":true,"runAsUser":101,"runAsGroup":101,"fsGroup":101,"seccompProfile":{"type":"RuntimeDefault"}},"containers":[{"name":"loader","volumeMounts":[{"name":"files","mountPath":"/srv/files"}],"securityContext":{"allowPrivilegeEscalation":false,"capabilities":{"drop":["ALL"]}}}],"volumes":[{"name":"files","persistentVolumeClaim":{"claimName":"prism-files"}}]}}' \
      -- sleep 3600
    P=$(kubectl -n nextcloud get pod -l app.kubernetes.io/component=app -o name | cut -d/ -f2)
    kubectl -n nextcloud cp -c nextcloud --retries=10 "$P:/var/www/html/data/asg/files/<folder>" ./prism-files
    kubectl -n prism exec loader -- find /srv/files -mindepth 1 -maxdepth 1 ! -name lost+found -exec rm -rf {} +
    kubectl -n prism cp --retries=10 ./prism-files/. loader:/srv/files

Verify, then clean up:

    S='cd "$0" && find . -path ./lost+found -prune -o -type f -exec sha256sum {} + | sort -k2'
    kubectl -n nextcloud exec "$P" -c nextcloud -- sh -c "$S" '/var/www/html/data/asg/files/<folder>' > src.sum
    kubectl -n prism exec loader -- sh -c "$S" /srv/files > dst.sum
    diff src.sum dst.sum && echo identical
    kubectl -n prism delete pod loader && rm -rf ./prism-files src.sum dst.sum

No restart needed; nginx serves the new files immediately.

## Rotate the password

Repeat setup step 1 (+ force-sync). nginx re-reads the htpasswd file per request, so no restart.

## Failure modes
- 401 with the right password → `prism-htpasswd` not synced, or the line isn't `user:$6$…`.
- 429 → the global nginx limit (all clients share it); wait a few seconds.
- 403 on a file → symlink (`disable_symlinks on`) or not readable by uid/gid 101; re-run the loader (fsGroup 101).
- Host unreachable but pod healthy → SNI allowlist or unproxied DNS; see `external-reach-recovery.md`.

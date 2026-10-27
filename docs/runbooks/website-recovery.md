# Runbook: Website (VPS)

`senger-solutions.com` → Cloudflare (proxied, Full strict) → `ristretto:443` nginx → `127.0.0.1:8080`
rootless Podman container `website` (Quadlet, user `website`). Roles: `vps/roles/{website,nginx}`.

## Deploy a new version
Renovate opens a `manual-deploy` PR bumping `website_image` (tag + digest). Merge it, then:

    cd vps && ansible-playbook playbook.yml -K --tags website,verify

`verify` fails if the running image differs from `website_image`. Old images: `sudo -u website
XDG_RUNTIME_DIR=/run/user/$(id -u website) podman image prune -af`.

## Status and logs
    sudo -u website XDG_RUNTIME_DIR=/run/user/$(id -u website) systemctl --user status website
    sudo -u website XDG_RUNTIME_DIR=/run/user/$(id -u website) journalctl --user -u website -n 100
    sudo tail /var/log/nginx/error.log

## CV PDFs
Not in git. Copy to `/srv/website/downloads/` (root-owned, `0644`):

    scp *.pdf ristretto:/tmp/ && ssh ristretto 'sudo install -m 0644 /tmp/*.pdf /srv/website/downloads/ && rm /tmp/*.pdf'

## ghcr token
Read-only on the package only (fine-grained if ghcr supports it, else classic `read:packages`).
Passed to `podman pull --creds` only when the image is missing; no auth file is written. Rotate: `sops edit
vps/group_vars/vps/secrets.sops.yml` → `website_ghcr_token`.

## Origin certificate
Cloudflare Origin CA, `senger-solutions.com`, 15 years; `website_origin_cert`/`website_origin_key`
in SOPS. Rotate: Cloudflare → SSL/TLS → Origin Server → Create Certificate, update SOPS, `--tags
nginx`, then revoke the old one.

## Authenticated Origin Pulls
Zone-level with our own CA (`vps/roles/nginx/files/aop-ca.pem`); Cloudflare's shared certificate
would admit any Cloudflare zone. A request without the client cert gets `400`.

Rotate (or rebuild): create a new chain (CA key is destroyed after signing):

    install -d -m 700 ~/aop && cd ~/aop
    openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -days 3650 \
      -subj "/CN=senger-solutions AOP CA" -keyout ca.key -out aop-ca.pem
    openssl req -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes \
      -subj "/CN=cloudflare-origin-pull" -keyout client.key -out client.csr
    openssl x509 -req -in client.csr -CA aop-ca.pem -CAkey ca.key -days 3650 \
      -extfile <(printf 'extendedKeyUsage=clientAuth\n') -out client.pem
    shred -u ca.key client.csr

Upload the client cert to the zone (API token with Zone → SSL and Certificates → Edit):

    curl -sX POST "https://api.cloudflare.com/client/v4/zones/$ZONE/origin_tls_client_auth" \
      -H "Authorization: Bearer $CF_TOKEN" -H 'Content-Type: application/json' \
      --data "$(jq -n --rawfile c client.pem --rawfile k client.key '{certificate:$c,private_key:$k}')"
    curl -sX PATCH "https://api.cloudflare.com/client/v4/zones/$ZONE/settings/tls_client_auth" \
      -H "Authorization: Bearer $CF_TOKEN" -H 'Content-Type: application/json' --data '{"value":"on"}'

Wait until the upload reports `active`, commit the new `aop-ca.pem`, `--tags nginx`, delete the old
zone certificate (`DELETE …/origin_tls_client_auth/<id>`), `shred -u client.key`.

## Site down
- `526`/`525` from Cloudflare → origin cert or SSL mode (must be Full (strict)).
- `400 No required SSL certificate` in a browser → AOP not enabled on the zone, or wrong CA on the
  VPS.
- `502` → container down: status/logs above; `--tags website` re-pulls if the image is missing.

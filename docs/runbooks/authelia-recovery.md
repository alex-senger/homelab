# Runbook: Authelia SSO (bring-up + recovery)

OIDC provider at `https://auth.senger-solutions.com`. In-repo (no chart), ns `authelia`,
wave 22, in-memory sessions, Postgres storage (`pg-rw.databases.svc`), file-based users via
ESO. OIDC clients: `argocd`, `grafana`, `nextcloud`. Passkeys (WebAuthn) enabled.

## Secrets model
Two ways secrets reach the config (matters when seeding):
- **Scalars** via `AUTHELIA_*_FILE` env (`deployment.yaml`): `SESSION_SECRET`,
  `STORAGE_ENCRYPTION_KEY`, `STORAGE_PASSWORD`, `JWT_SECRET`, `OIDC_HMAC_SECRET`.
- **List-of-object fields** (`oidc.jwks[].key`, `clients[].client_secret`) have no `_FILE`
  form → injected via the Go template filter in `configmap.yaml` (`{{ secret … }}`), enabled by
  `X_AUTHELIA_CONFIG_FILTERS=template`. Don't add indexed `_FILE` vars.

Source of truth for OpenBao paths: `secrets-externalsecret.yaml`, `users-externalsecret.yaml`,
`argocd/oidc-clientsecret-externalsecret.yaml`.

## Prerequisites (in main)
- cilium `envoy.xdsMode: split` (1.20 'ads' breaks Gateway L7 xDS).
- Gateway API CRDs v1.6.1 **experimental** channel, full set (Cilium 1.20 aborts without
  ReferenceGrant v1 / TLSRoute / BackendTLSPolicy).
- After either lands: `kubectl -n kube-system rollout restart deploy/cilium-operator ds/cilium ds/cilium-envoy`.
  Cilium re-syncs the wildcard cert into `cilium-secrets` as `cilium-sync-secret-<hash>` — check
  there first if Gateway TLS fails after a restart.

## Seed OpenBao (before/after merge — ESO retries)
`secret/databases/authelia` (property `password`) already exists from #7c; don't reseed.

Generate: `openssl rand -hex 64` each for session / storage-encryption-key / jwt / oidc-hmac;
`openssl genrsa -out issuer.pem 4096` for the RS256 issuer key.

Per OIDC client (argocd/grafana/nextcloud) — plaintext + argon2 hash. **Hash it, strip the
`Digest: ` label, seed via `@file` (never inline in `sh -c`)** or the `$` gets shell-mangled and
Authelia stores it as plaintext → `invalid_client`:

    SECRET=$(openssl rand -hex 32)
    kubectl -n authelia exec deploy/authelia -- authelia crypto hash generate argon2 \
      --password "$SECRET" --no-confirm | sed -n 's/^Digest: //p' > app.hash   # must start with $argon2id$

`users_database.yml` (file backend): admin + asg, with groups `argocd-admins` / `grafana-admins`
for RBAC. Copy multi-line files into the pod, then write (`@file` resolves inside the pod):

    kubectl -n openbao cp issuer.pem openbao-0:/tmp/issuer.pem   # + app hashes, users_database.yml
    kubectl -n openbao exec -it openbao-0 -- sh -c 'BAO_TOKEN=<root> bao kv put secret/authelia/oidc \
      hmac=<…> issuer-private-key=@/tmp/issuer.pem \
      argocd-client-secret=<plain> argocd-client-secret-hash=@/tmp/argocd.hash …'
    kubectl -n openbao exec -it openbao-0 -- sh -c 'BAO_TOKEN=<root> bao kv put secret/authelia/users users-yaml=@/tmp/users_database.yml'
    # also: secret/authelia/{session,storage,jwt}, and secret/{grafana,nextcloud}/oidc for those clients

Then shred local plaintext + remove the pod copies. Verify: `kubectl -n authelia get externalsecret`
→ SecretSynced; pod Running.

## DNS
`auth.senger-solutions.com` → in-cluster via CoreDNS (`infrastructure/coredns` → `.3`); LAN via
laptop `/etc/hosts` → `.3`; public via the VPS nginx SNI relay → `10.10.0.2:443`.

## Verify
- Portal loads at `auth.`; admin logs in.
- Passkey/TOTP enrollment at `auth.senger-solutions.com/settings` (visible only because
  grafana/argocd clients require `two_factor` — authelia#9664). Notifier is filesystem:
  `kubectl -n authelia exec deploy/authelia -- cat /data/notification.txt`.
- OIDC: argocd/grafana/nextcloud → "Log in with Authelia".

## Failure modes
| Symptom | Cause / check |
| --- | --- |
| `invalid_client` at token exchange | seeded hash has the `Digest: ` label or was `$`-mangled. `kubectl -n authelia get secret authelia-secrets -o jsonpath='{.data.OIDC_<APP>_CLIENT_SECRET_HASH}' \| base64 -d \| head -c 12` must be `$argon2id$`; re-hash via `@file`. |
| `invalid_client` with `client_secret_post`/`_basic` mismatch | set the client's `token_endpoint_auth_method` to what the app sends (nextcloud user_oidc = post; argocd/grafana = basic default). |
| OIDC discovery 525/timeout from a pod | in-cluster `auth.` resolution — handled by CoreDNS (`auth`→`.3`). Check `getent hosts auth.senger-solutions.com` in the pod = `.3`. |
| Passkey/2FA registration option missing in settings | Authelia hides it unless a policy requires `two_factor` (authelia#9664). |
| pod `CreateContainerConfigError` | ExternalSecret not Ready / key typo — `describe externalsecret`. |
| CrashLoop, storage errors | Postgres unreachable, or `STORAGE_PASSWORD` ≠ `secret/databases/authelia`. |
| redirect_uri mismatch | client `redirect_uris` in `configmap.yaml` vs what the app requests. |

# Runbook: Authelia SSO bring-up and recovery

Covers the one-time bring-up of Authelia (sub-project #7b): the two Cilium/Gateway API
prerequisites the forward-auth path depends on, generating and seeding the OpenBao secrets
Authelia and ArgoCD's OIDC client need, local DNS, end-to-end verification (OIDC login + forward
auth), and the failure modes to check first when something in this chain breaks later.

## Status: not yet brought up

`infrastructure/authelia/`, the ArgoCD OIDC/route changes in `infrastructure/argocd/`, and the
`ExternalAuth` filter on `infrastructure/gateway/whoami.yaml` are all merged and reconcile once
ArgoCD syncs them, but **no OpenBao secret under `secret/authelia/*` has been seeded yet**, so the
`authelia-secrets` / `authelia-users` `ExternalSecret`s cannot resolve and the Authelia pod cannot
start. This runbook is the checklist for the first operator to bring it up, and the reference for
recovering the same pieces later.

## Architecture recap (why the pieces below exist)

- Authelia runs in-repo (no chart) in namespace `authelia`, ArgoCD wave 22, in-memory sessions, a
  Postgres storage backend (`pg-rw.databases.svc`), and a **file** authentication backend
  (`authentication_backend.file.path: /users/users_database.yml`, sourced from OpenBao via ESO).
- **Secrets reach Authelia's config two different ways** — this matters when seeding OpenBao:
  - Five plain scalars use Authelia's standard `AUTHELIA_*_FILE` env-var convention
    (`infrastructure/authelia/deployment.yaml`): `SESSION_SECRET`, `STORAGE_ENCRYPTION_KEY`,
    `STORAGE_PASSWORD`, `JWT_SECRET`, `OIDC_HMAC_SECRET`.
  - The two secrets inside `identity_providers.oidc` that are **list-of-object fields**
    (`jwks[0].key`, `clients[0].client_secret`) have **no indexed `AUTHELIA_..._FILE` form** —
    Authelia's own docs state this is unsupported for lists of objects. Instead
    `infrastructure/authelia/configmap.yaml` reads them at render time via Authelia's Go-template
    config filter (`{{ secret "/secrets/OIDC_ISSUER_PRIVATE_KEY" | mindent 14 "|" | msquote }}`
    and the equivalent for `OIDC_ARGOCD_CLIENT_SECRET_HASH`), turned on by the
    `X_AUTHELIA_CONFIG_FILTERS=template` env var. Both still read from files mounted from the same
    `authelia-secrets` Secret — only the consumption mechanism differs. If you go looking for
    `AUTHELIA_IDENTITY_PROVIDERS_OIDC_JWKS_0_KEY_FILE` anywhere, it does not exist; do not add it.
- The exact OpenBao paths/properties to seed are defined by the `ExternalSecret`s themselves —
  treat `infrastructure/authelia/secrets-externalsecret.yaml`,
  `infrastructure/authelia/users-externalsecret.yaml`, and
  `infrastructure/argocd/oidc-clientsecret-externalsecret.yaml` as the source of truth if this
  runbook and the manifests ever drift.
- ArgoCD's OIDC client secret is **Merged** (not owned) into the chart-managed `argocd-secret`,
  because ArgoCD's `$oidc.argocd.clientSecret` reference (no colon) only resolves a **bare key
  inside `argocd-secret` itself** — a separate, ESO-owned Secret would never be read.
- whoami is gated by a native Gateway API `ExternalAuth` HTTPRoute filter (GEP-1494), **not** a
  hand-rolled `CiliumEnvoyConfig`/`ext_authz` filter chain — that approach was tried and found
  unworkable on this cluster (see below).

## Prerequisite: Cilium ≥1.20 + Gateway API experimental CRDs (already in `main`)

The `ExternalAuth` HTTPRoute filter that gates `whoami` does not exist before these two changes,
both already merged ahead of this branch:

1. **`infrastructure/cilium/values.yaml`: `envoy.xdsMode: split`.** Cilium 1.20's default xDS
   mode (`ads`, sometimes called xdsnew) was found to break Gateway API's L7 xDS delivery on this
   cluster; `split` is required for Gateway-managed listeners to receive filter config reliably.
2. **`infrastructure/gateway-api-crds`: Gateway API CRDs pinned to `v1.6.1`, `experimental`
   channel** (not `standard`). The `ExternalAuth` filter type and its `externalAuth` schema field
   are an **experimental-channel-only** feature — confirmed by direct inspection of the installed
   `httproutes.gateway.networking.k8s.io` CRD (Task 6 of this sub-project hit this as a hard
   blocker before the CRD swap: the standard-channel v1.6.1 bundle's filter `type` enum has no
   `ExternalAuth` member and no `externalAuth` object field at all). The experimental bundle also
   carries `GRPCRoute`, `ReferenceGrant` v1, `TLSRoute`, and `BackendTLSPolicy` — **Cilium 1.20's
   Gateway controller requires all of these to be present or it aborts reconciliation entirely**
   (not just for the new feature — it will stop syncing Gateway TLS secrets cluster-wide, taking
   down every route on the `external` Gateway, not only `whoami`). The component pulls all 7
   Gateway API CRDs (`gatewayclasses`, `gateways`, `httproutes`, `grpcroutes`, `referencegrants`,
   `tlsroutes`, `backendtlspolicies`); do not "trim" the experimental CRD list down to just
   `httproutes`/`referencegrants` — install the full set the kustomization already pulls in.

**Cilium restart required after either lands** (this is the general rule from
[cilium-recovery.md](cilium-recovery.md) — a `values.yaml`/CRD change updates config but nothing
restarts the pods that read it at startup):

    kubectl -n kube-system rollout restart deploy/cilium-operator ds/cilium ds/cilium-envoy

Confirm activation:

    kubectl explain httproute.spec.rules.filters.type
    # enum must include ExternalAuth
    kubectl explain httproute.spec.rules.filters.externalAuth --recursive
    # must print backendRef / http.path / http.allowedHeaders / http.allowedResponseHeaders / protocol

As part of this restart, Cilium (re-)syncs the wildcard Gateway TLS certificate
(`wildcard-senger-solutions-tls` in `gateway`) into the `cilium-secrets` namespace as a
generated-name Secret (observed as `cilium-sync-secret-<hash>` on this cluster — the exact hash is
not stable across syncs). If HTTPS on the `external` Gateway starts failing TLS handshakes right
after this restart, check that a synced secret exists in `cilium-secrets` before looking anywhere
else:

    kubectl -n cilium-secrets get secrets

If both of the above are already true on the running cluster (check first — they may already be
merged and active by the time you run this), skip straight to seeding OpenBao.

## Bring-up order

1. **Seed OpenBao** (`secret/authelia/*`, this runbook, below) — do this before or immediately
   after merge; ArgoCD will retry until it resolves.
2. **Merge `feat/authelia-sso` to `main`.** ArgoCD syncs `infrastructure/authelia` (wave 22),
   the ArgoCD OIDC/route change (`infrastructure/argocd`), and the `whoami` `ExternalAuth` filter
   + `ReferenceGrant` (`infrastructure/gateway`, `infrastructure/authelia`).
3. **Expect the Authelia pod to sit in `CreateContainerConfigError`** until both
   `ExternalSecret`s resolve — this is the same bootstrap ordering every other ESO-backed
   workload in this cluster goes through (OpenBao/Garage). Force-sync the `authelia-secrets` /
   `authelia-users` `ExternalSecret`s if you're impatient:

       kubectl -n authelia get externalsecret
       kubectl -n authelia annotate externalsecret authelia-secrets force-sync=$(date +%s) --overwrite
       kubectl -n authelia annotate externalsecret authelia-users force-sync=$(date +%s) --overwrite

4. **Add local DNS** (below), then verify (below).

## Seed OpenBao

All paths below are under the `secret` KV v2 mount (`ClusterSecretStore` `openbao`,
`spec.provider.vault.path: secret`), matching the `remoteRef.key` values actually configured in
the `ExternalSecret`s. `secret/databases/authelia` (`property: password`) already exists from
sub-project #7c — verify it, don't reseed it, unless you're rotating it.

    export KUBECONFIG=/tmp/rk.yaml
    kubectl -n openbao get externalsecret -n databases authelia-db   # sanity check only

### 1. Generate the material (operator's machine)

Four random secrets — Authelia has no hard minimum above roughly 20-64 bytes depending on the
field, so a uniform 64-byte hex string for all four is a safe, documented-compatible choice:

    openssl rand -hex 64 > session.secret
    openssl rand -hex 64 > storage-encryption-key.secret
    openssl rand -hex 64 > jwt.secret
    openssl rand -hex 64 > oidc-hmac.secret

OIDC issuer signing key (RS256, matches `jwks[0].algorithm: RS256` in `configmap.yaml`):

    openssl genrsa -out issuer-private-key.pem 4096

ArgoCD OIDC client secret — generate the plaintext once, then hash it with Authelia's own CLI
(pin the same image tag the Deployment runs, `4.39.26`, so the argon2 parameters match what the
running Authelia expects to verify against):

    openssl rand -hex 32 > argocd-client-secret.plain
    docker run --rm authelia/authelia:4.39.26 authelia crypto hash generate argon2 \
      --password "$(cat argocd-client-secret.plain)" | sed -n 's/^Digest: //p' > argocd-client-secret.hash
    head -c 12 argocd-client-secret.hash   # sanity: MUST print "$argon2id$"
    # The CLI prints "Digest: $argon2id$..."; strip the "Digest: " label so the file
    # holds ONLY the $argon2id$ digest. Seeding the whole line (or letting the shell
    # expand the $ signs) leaves Authelia an unparseable value it treats as a
    # PLAINTEXT secret -> ArgoCD OIDC token exchange fails with `invalid_client`.
    # This is why the hash is copied into the pod and seeded via @file below, never
    # inline in the `sh -c`.

Admin user's password, same way, then build `users_database.yml` (file authentication backend,
path `/users/users_database.yml`). Give the admin user the `argocd-admins` group so ArgoCD's
`g, argocd-admins, role:admin` RBAC policy (`infrastructure/argocd/values.yaml`) actually grants
admin on login:

    docker run --rm authelia/authelia:4.39.26 authelia crypto hash generate argon2 \
      --password '<CHOOSE_ADMIN_PASSWORD>'
    # Output is "Digest: $argon2id$...". Paste ONLY the $argon2id$... part below
    # (drop the "Digest: " label), quoted, so Authelia parses it as a real hash.
    cat > users_database.yml <<'EOF'
    users:
      admin:
        disabled: false
        displayname: "Admin"
        password: "<PASTE_ARGON2_DIGEST_FROM_ABOVE>"
        email: admin@senger-solutions.com
        groups:
          - admins
          - argocd-admins
    EOF

### 2. Copy multi-line material into the OpenBao pod

`bao kv put key=@file` resolves the file relative to wherever the `bao` process runs — that's
*inside* the `openbao-0` pod when invoked via `kubectl exec`, not your laptop. Copy the two
multi-line files in first (matches the existing `wg0.conf=@/path/...` pattern in
[openbao-recovery.md](openbao-recovery.md)):

    export KUBECONFIG=/tmp/rk.yaml
    kubectl -n openbao cp issuer-private-key.pem openbao-0:/tmp/issuer-private-key.pem
    kubectl -n openbao cp users_database.yml openbao-0:/tmp/users_database.yml
    kubectl -n openbao cp argocd-client-secret.hash openbao-0:/tmp/argocd-client-secret.hash

### 3. Write the KV entries

Using the root token from [openbao-recovery.md](openbao-recovery.md) (or a token with write
access under `secret/authelia/*`):

    kubectl -n openbao exec -it openbao-0 -- sh -c \
      "BAO_TOKEN=<root-token> bao kv put secret/authelia/session secret=$(cat session.secret)"
    kubectl -n openbao exec -it openbao-0 -- sh -c \
      "BAO_TOKEN=<root-token> bao kv put secret/authelia/storage encryption-key=$(cat storage-encryption-key.secret)"
    kubectl -n openbao exec -it openbao-0 -- sh -c \
      "BAO_TOKEN=<root-token> bao kv put secret/authelia/jwt secret=$(cat jwt.secret)"
    kubectl -n openbao exec -it openbao-0 -- sh -c \
      'BAO_TOKEN=<root-token> bao kv put secret/authelia/oidc \
         hmac='"$(cat oidc-hmac.secret)"' \
         issuer-private-key=@/tmp/issuer-private-key.pem \
         argocd-client-secret='"$(cat argocd-client-secret.plain)"' \
         argocd-client-secret-hash=@/tmp/argocd-client-secret.hash'
    kubectl -n openbao exec -it openbao-0 -- sh -c \
      'BAO_TOKEN=<root-token> bao kv put secret/authelia/users users-yaml=@/tmp/users_database.yml'

Then remove the copies from inside the pod and shred the local plaintext, same discipline as
`openbao-recovery.md` uses for other secret material:

    kubectl -n openbao exec -it openbao-0 -- rm -f /tmp/issuer-private-key.pem /tmp/users_database.yml /tmp/argocd-client-secret.hash
    shred -u session.secret storage-encryption-key.secret jwt.secret oidc-hmac.secret \
      issuer-private-key.pem argocd-client-secret.plain argocd-client-secret.hash users_database.yml

### 4. Confirm the ExternalSecrets resolved

    kubectl -n authelia get externalsecret authelia-secrets authelia-users
    kubectl -n argocd get externalsecret argocd-oidc-clientsecret
    # all three: SecretSynced / Ready=True
    kubectl -n authelia get pods
    # authelia pod should now be Running, not CreateContainerConfigError

## DNS

- `auth.senger-solutions.com` and `argocd.senger-solutions.com` both resolve to the Gateway's
  reserved LoadBalancer IP, `192.168.178.3`, on the LAN (FritzBox local DNS or hosts entries).
- `auth.` is also relayed from outside the LAN: add it to the VPS `nginx` `stream {}` SNI map
  documented in `vps/reference/nginx-stream-snippet.conf` (that file is reference-only — it is not
  applied by this repo's Ansible — so this is a manual edit on the VPS itself), pointing at the
  same `10.10.0.2:443` WireGuard-tunnel upstream already used for `whoami.senger-solutions.com`.
- `argocd.` is **LAN-only by design** (control-plane UI) — do **not** add it to the VPS SNI map.
  `infrastructure/argocd/httproute.yaml` is deliberately attached to the Gateway for a valid
  wildcard cert and a clean hostname, nothing more.

## Verify

1. **Portal:** `https://auth.senger-solutions.com` loads the Authelia login page and accepts the
   admin credentials seeded above.
2. **TOTP enrollment:** Authelia's notifier is filesystem-based (no SMTP configured), so the
   enrollment/reset link is written to a file instead of emailed:

       kubectl -n authelia exec deploy/authelia -- cat /data/notification.txt

   Open the link it prints to enroll TOTP for the admin user.
3. **ArgoCD OIDC login:** `https://argocd.senger-solutions.com` → "Log in via Authelia" → after
   authenticating, the `argocd-admins` group claim should grant `role:admin`
   (`infrastructure/argocd/values.yaml` `rbac.policy.csv`). Confirm in the UI (Settings → your
   user) that the `groups` claim actually came through — if RBAC falls back to read-only, the
   `groups` scope/claim likely didn't reach ArgoCD (see failure modes below).
4. **whoami forward-auth:** `https://whoami.senger-solutions.com` should redirect to Authelia
   (portal or an inline auth challenge) and, after authenticating, redirect back and render
   traefik/whoami's output. This confirms the `ExternalAuth` filter, the `ReferenceGrant`, and
   Authelia's `/api/authz/ext-authz/` endpoint are all wired correctly end to end.

   Also confirm the target host is actually reaching Authelia, not just that *some* challenge
   appeared: trigger a whoami request, then tail Authelia's access log —

       kubectl -n authelia logs deploy/authelia | grep -i whoami

   — and confirm the log line shows the `whoami.senger-solutions.com` host and that the
   `whoami → one_factor` `access_control` rule matched, not a `default_policy: bypass`
   fall-through (Authelia logs which rule/policy it applied per request at `debug` level).
   **If `https://whoami.senger-solutions.com` loads immediately with NO auth challenge at all,**
   the Host/authority is most likely never reaching Authelia — check that `host` and the
   `x-forwarded-*` headers Authelia's ExtAuthz implementation needs (`x-forwarded-proto`,
   `x-forwarded-for`) are present in the whoami `HTTPRoute`'s
   `externalAuth.http.allowedHeaders` (`infrastructure/gateway/whoami.yaml`) before assuming the
   fails-open behavior below is in play.

## Fails-open verification (do this — it's a known Cilium 1.20 limitation, not optional)

GEP-1494's `ExternalAuth` field documentation states the filter **must fail closed** if the auth
backend is unreachable. Cilium 1.20's implementation is reported (cilium/cilium#47178) to instead
**fail open** — serving the backend unauthenticated rather than denying — which is the opposite of
what `whoami`'s `one_factor` policy is supposed to guarantee. This was flagged as the single
biggest operational risk of this mechanism during implementation (Task 6) and was explicitly
deferred to this runbook to test against the live cluster. Run it now, before trusting the gate
for anything more sensitive than the demo `whoami` app:

    kubectl -n authelia scale deploy/authelia --replicas=0
    kubectl -n authelia get pods -w   # wait for the pod to actually terminate
    curl -sk -o /dev/null -w '%{http_code}\n' https://whoami.senger-solutions.com/

- **`403`/`401`/connection refused with no whoami body → fail-closed (desired).** The
  `ExternalAuth` filter is behaving per spec on this cluster/version.
- **`200` with whoami's output served → fail-open (the known #47178 limitation).** Record which
  one you observed here and in the sub-project's notes: if it fails open, `whoami` (and anything
  gated the same way later) is reachable unauthenticated whenever Authelia is down, degraded, or
  mid-rollout — treat that as a standing, documented limitation of this bring-up, not a
  misconfiguration to keep chasing. Re-test after any Cilium minor upgrade; this is exactly the
  kind of thing that gets fixed silently in a point release.

Scale Authelia back up regardless of the result:

    kubectl -n authelia scale deploy/authelia --replicas=1

## Failure modes

| Symptom | Likely cause | Check |
| --- | --- | --- |
| `whoami.senger-solutions.com` loads with no auth challenge at all | `ExternalAuth` filter not applied — Cilium not yet restarted after the CRD/config change, or still on the standard-channel CRDs; **or** the filter is applied but the target host / `X-Forwarded-*` headers aren't reaching Authelia (not in `allowedHeaders`), so `default_policy: bypass` matched instead of the `whoami → one_factor` rule | `kubectl explain httproute.spec.rules.filters.type` (must list `ExternalAuth`); `kubectl -n gateway get httproute whoami -o yaml` (filter block present and not rejected, `http.allowedHeaders` includes `host`/`x-forwarded-proto`/`x-forwarded-for`); `kubectl -n authelia logs deploy/authelia \| grep -i whoami` (confirm the host and matched rule) |
| `whoami` request hangs then 5xx, or ArgoCD/Authelia TLS handshakes fail right after a Cilium restart | Wildcard cert not yet re-synced into `cilium-secrets` | `kubectl -n cilium-secrets get secrets`; restart `cilium-operator` again if empty |
| ArgoCD OIDC login redirects back to ArgoCD but session isn't admin (falls back to readonly) | `groups` claim not reaching ArgoCD, or `argocd-admins` not on the user in `users_database.yml` | Check `requestedIDTokenClaims.groups.essential: true` rendered into `argocd-cm`; check the user's `groups:` list in the seeded `users_database.yml`; check ArgoCD server logs for the parsed ID token claims |
| OIDC login fails with a redirect_uri / state / host mismatch error | `redirect_uris` in `configmap.yaml` (`https://argocd.senger-solutions.com/auth/callback`, `http://localhost:8085/auth/callback`) don't match what ArgoCD actually requests, or DNS for `argocd.` isn't pointed at the Gateway yet | Compare the error's `redirect_uri` param against `configmap.yaml`'s `clients[0].redirect_uris`; confirm `argocd.senger-solutions.com` resolves to `192.168.178.3` |
| ArgoCD login: `failed to query provider "https://auth.senger-solutions.com": 525` (long hang first) | argocd-server does OIDC discovery SERVER-SIDE; `auth.` resolves publicly to Cloudflare/VPS from in-cluster and the TLS leg fails. Fixed by `global.hostAliases` in `infrastructure/argocd/values.yaml` pinning `auth.senger-solutions.com` → `192.168.178.3` | `kubectl -n argocd exec deploy/argocd-server -- cat /etc/hosts` (must list `192.168.178.3 auth.senger-solutions.com`); roll argocd-server after adding it |
| ArgoCD login: `failed to get token: oauth2: "invalid_client"` | The `client_secret` Authelia stores for `argocd` isn't a valid `$argon2id$` hash of the plaintext ArgoCD sends — usually the seeded `argocd-client-secret-hash` still has the `Digest: ` label or was `$`-mangled by the shell (Authelia then logs it as "plaintext" at startup) | `kubectl -n authelia get secret authelia-secrets -o jsonpath='{.data.OIDC_ARGOCD_CLIENT_SECRET_HASH}' \| base64 -d \| head -c 12` (MUST be `$argon2id$`); if not, re-hash the existing plaintext (`kubectl -n argocd get secret argocd-secret -o jsonpath='{.data.oidc\.argocd\.clientSecret}' \| base64 -d`) with `... \| sed -n 's/^Digest: //p' > ach.hash`, `bao kv patch secret/authelia/oidc argocd-client-secret-hash=@/tmp/ach.hash`, force-sync ESO, restart Authelia |
| Authelia pod `CrashLoopBackOff` after `CreateContainerConfigError` clears, storage errors in logs | Postgres unreachable, or `STORAGE_PASSWORD` doesn't match `secret/databases/authelia` | `kubectl -n authelia logs deploy/authelia`; `kubectl -n databases get cluster pg`; re-verify `authelia-db` `ExternalSecret` resolved the same password Authelia was seeded with |
| whoami always redirects to Authelia but Authelia itself returns 401/redirect loop for a valid session | Session cookie domain scope mismatch — `session.cookies[0].domain: senger-solutions.com` must be a suffix match for both `auth.` and `whoami.` hosts | Inspect the `authelia_session` cookie's `Domain=` attribute in the browser; confirm it's `senger-solutions.com`, not `auth.senger-solutions.com` |
| Authelia pod `CreateContainerConfigError` never clears even after OpenBao is seeded | `authelia-secrets` / `authelia-users` `ExternalSecret` still not `Ready`, or a key name typo when seeding (`secretKey` in the `ExternalSecret` must exist as written in the `remoteRef.property`) | `kubectl -n authelia describe externalsecret authelia-secrets`; `kubectl -n openbao exec -it openbao-0 -- sh -c 'BAO_TOKEN=<token> bao kv get secret/authelia/oidc'` and diff the property names against `secrets-externalsecret.yaml` |
| `jwks[0].key` / `clients[0].client_secret` render as the literal `{{ secret ... }}` string, or Authelia fails to parse `configuration.yml` | `X_AUTHELIA_CONFIG_FILTERS=template` missing from the Deployment env, or the referenced `/secrets/OIDC_*` file isn't mounted | `kubectl -n authelia exec deploy/authelia -- env \| grep X_AUTHELIA_CONFIG_FILTERS`; `kubectl -n authelia exec deploy/authelia -- ls /secrets` |
| Everything above looks correct but `whoami`'s `ExternalAuth` still doesn't gate anything | Cilium 1.20 fails open (#47178) | Run the fails-open test above; if it fails open, this is the documented limitation, not a new bug |

## Do not

- Do not reintroduce indexed `AUTHELIA_IDENTITY_PROVIDERS_OIDC_JWKS_0_KEY_FILE` /
  `..._CLIENTS_0_CLIENT_SECRET_FILE` env vars — Authelia does not support `_FILE` secrets for
  list-of-object config sections; use the template filter already in `configmap.yaml`.
- Do not set `oidc-clientsecret-externalsecret.yaml`'s `creationPolicy` to `Owner` — `argocd-secret`
  is owned by the argo-cd Helm chart, which writes other runtime keys into it; `Merge` is required.
- Do not add `argocd.senger-solutions.com` to the VPS SNI relay map.
- Do not trust the `whoami` `ExternalAuth` gate as a real access boundary until you've run the
  fails-open test above at least once on the current Cilium version.

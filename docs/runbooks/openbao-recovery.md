# Runbook: OpenBao init, unseal, recovery

Single-node raft, ns `openbao`. Auto-unseal via the `openbao-unsealer` Deployment (SOPS-decrypts
the committed Shamir keys with a dedicated age key). ESO authenticates via Kubernetes auth (role
`eso`). Rationale: secrets-eso-openbao design spec.

## Out-of-band Secrets (never committed)
- **`openbao-unseal-age-key`** (ns `openbao`): dedicated age key (NOT the Talos age key) that
  decrypts `infrastructure/openbao-unsealer/unseal-keys.sops.yaml`.

      age-keygen -o /tmp/k.key   # keep the printed public key
      kubectl -n openbao create secret generic openbao-unseal-age-key --from-file=key.txt=/tmp/k.key
      shred -u /tmp/k.key

- **`openbao-root-token`** (ns `openbao`): consumed by the `openbao-config` Job.
  `kubectl -n openbao create secret generic openbao-root-token --from-literal=token=<…>`.

## One-time init
    kubectl -n openbao exec -it openbao-0 -- bao operator init -key-shares=<N> -key-threshold=<M>

Capture the unseal keys + root token (shown once). Then:
1. SOPS-encrypt the keys to the dedicated age pubkey → commit over `unseal-keys.sops.yaml` (confirm
   it's encrypted before pushing).
2. Create the two out-of-band Secrets above.
3. `kubectl -n openbao delete pod -l app=openbao-unsealer` → it unseals.
4. Run the idempotent `openbao-config` Job (KV v2, k8s auth, `eso-read` policy, `eso` role).
5. Seed adopted secrets: `secret/cert-manager/cloudflare-api-token` (`api-token`).
6. Verify the ExternalSecrets are SecretSynced.

## Auto-unseal
Init container SOPS-decrypts to a tmpfs emptyDir; the main container loops the unseal API until
`sealed=false`. Runs on every restart (incl. Talos node rolls); fails safe (stays sealed) if it
can't decrypt or reach OpenBao.

## Manual unseal (only if the companion is broken)
    kubectl -n openbao exec -it openbao-0 -- bao operator unseal   # repeat M times
    kubectl -n openbao exec -it openbao-0 -- bao status            # Sealed: false

## Root token hygiene
Only needed for init and re-running `openbao-config`. Revoke after: `bao token revoke -self`.
Regenerate via `bao operator generate-root` when the Job must run again (e.g. after editing the
`eso-read` policy).

## ESO auth (role `eso`)
No static credential: the `external-secrets` SA logs in as `eso` (read-only on
`secret/data|metadata/*`). Store `openbao` → `http://openbao.openbao.svc:8200`, mount `kubernetes`,
role `eso`. On auth errors check: `openbao-config` completed, the SA name/namespace bindings, and
OpenBao is unsealed.

## OIDC login (Authelia)
UI/CLI login via Authelia client `openbao` (2FA); role `admin` requires group `openbao-admins` and
grants the `admin` policy (1h token). Not on the Gateway — reach it via port-forward:

    kubectl -n openbao port-forward svc/openbao 8200   # UI: http://localhost:8200 → OIDC
    BAO_ADDR=http://localhost:8200 bao login -method=oidc   # CLI, callback on :8250

Seed `secret/openbao/oidc` (`client-secret` + `client-secret-hash`, see authelia-recovery.md), then
re-run `openbao-config`; it skips OIDC while the `openbao-oidc` Secret is absent. Once OIDC works,
revoke the root token. `invalid_redirect_uri`/`claim "groups" does not match` → the port must be
8200 (UI) / 8250 (CLI) and the user needs `openbao-admins`.

## Health
    kubectl -n openbao get pods
    kubectl -n openbao exec -it openbao-0 -- bao status
    kubectl -n external-secrets get pods

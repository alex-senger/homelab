# Runbook: OpenBao bring-up, unseal, and recovery

Covers the one-time initialization OpenBao needs before it holds any data, the two Secrets that
exist only on-cluster (never committed), how the committed SOPS-encrypted unseal keys are used,
manual unsealing, root-token hygiene, and how ESO authenticates once OpenBao is live. See
[ADR 0004](../decisions/0004-secrets-eso-openbao.md) for why each piece exists.

## Status: not yet initialized

Everything this runbook describes is designed and merged — the OpenBao chart, the
`openbao-unsealer` companion, the `openbao-config` bootstrap Job, ESO, and the `ClusterSecretStore`
all reconcile today. **OpenBao itself has never been initialized.** It starts sealed and empty;
the two `ExternalSecret`s (`cloudflare-api-token`, `wg-ingress-key`) cannot resolve and the
Kubernetes Secrets they target do not yet exist. This section is the checklist for the first
operator to bring it up; every step below that produces key material is manual by design — no
manifest performs it.

## Out-of-band Secrets

As with `talos/talsecret.sops.yaml` and the two secrets tracked in
[external-reach-recovery.md](external-reach-recovery.md), this sub-project knowingly keeps two
Secrets outside Git, encrypted or otherwise:

### `openbao-unseal-age-key` (namespace `openbao`)

The private half of an age keypair **dedicated to OpenBao** — never the Talos master age key used
elsewhere in this repository (see ADR 0004's blast-radius reasoning). It decrypts
`infrastructure/openbao-unsealer/unseal-keys.sops.yaml`, the SOPS-encrypted Shamir unseal keys
committed to Git.

To create it for the first time:

    age-keygen -o /tmp/openbao-unseal.key   # prints the public key to stderr; keep it
    export KUBECONFIG=/tmp/rk.yaml
    kubectl -n openbao create secret generic openbao-unseal-age-key \
      --from-file=key.txt=/tmp/openbao-unseal.key
    shred -u /tmp/openbao-unseal.key   # do not leave the private key on disk

Keep the printed public key — it is the recipient for the `sops -e` step below.

### `openbao-root-token` (namespace `openbao`)

The OpenBao root token, consumed by the `openbao-config` bootstrap Job to enable the KV engine,
Kubernetes auth, and the `eso` role. Only ever needed for that Job and for manual `bao` CLI
operations; see **Revoke the root token after bootstrap** below.

To create it:

    export KUBECONFIG=/tmp/rk.yaml
    kubectl -n openbao create secret generic openbao-root-token \
      --from-literal=token='<PASTE_ROOT_TOKEN>'

## One-time init (`bao operator init`)

Run once, against a freshly deployed, uninitialized OpenBao:

    export KUBECONFIG=/tmp/rk.yaml
    kubectl -n openbao exec -it deploy/openbao -- \
      bao operator init -key-shares=<N> -key-threshold=<M>

This prints `<N>` unseal keys and one root token. **Capture all of it immediately — it is shown
exactly once and cannot be retrieved from OpenBao afterward.**

1. SOPS-encrypt the unseal keys to the dedicated age public key from the step above, and commit
   the result over the placeholder file:

       sops --encrypt --age <OPENBAO_UNSEAL_AGE_PUBLIC_KEY> \
         /tmp/unseal-keys.yaml > infrastructure/openbao-unsealer/unseal-keys.sops.yaml
       shred -u /tmp/unseal-keys.yaml

   Confirm the committed file is genuinely SOPS-encrypted (has `sops:` metadata, no plaintext key
   values) before pushing — the same property CI already enforces for
   `talos/talsecret.sops.yaml`.
2. Create `openbao-unseal-age-key` and `openbao-root-token` as shown above.
3. Delete the `openbao-unsealer` pod (or wait for its loop) so it picks up the real keys and
   unseals OpenBao:

       kubectl -n openbao delete pod -l app=openbao-unsealer

4. Let ArgoCD sync (or manually trigger) the `openbao-config` Job. It is idempotent — safe to
   re-run — and applies the KV v2 engine, Kubernetes auth, the `eso-read` policy, and the `eso`
   role.
5. Seed the two adopted secrets:

       kubectl -n openbao exec -it deploy/openbao -- sh -c \
         'BAO_TOKEN=<root-token> bao kv put secret/cert-manager/cloudflare-api-token api-token=<CLOUDFLARE_TOKEN>'
       kubectl -n openbao exec -it deploy/openbao -- sh -c \
         'BAO_TOKEN=<root-token> bao kv put secret/wg-ingress/wg-ingress-key wg0.conf=@/path/to/wg0.conf'

6. Confirm the `ExternalSecret`s resolve:

       kubectl -n cert-manager get externalsecret cloudflare-api-token
       kubectl -n wg-ingress get externalsecret wg-ingress-key

   Both should show `SecretSynced` / `Ready=True` and produce a Kubernetes Secret with the
   expected keys (`api-token`, `wg0.conf`).

## How the unsealer uses the committed keys

`openbao-unsealer` (`infrastructure/openbao-unsealer/`) is a separate Deployment, not a sidecar in
the OpenBao pod. Its init container decrypts the SOPS ciphertext into a memory-backed `emptyDir`
using `SOPS_AGE_KEY_FILE` pointed at the mounted `openbao-unseal-age-key` Secret; its main
container then loops calling the unseal API until OpenBao reports `sealed=false`, harmlessly
no-op once already unsealed. Nothing it handles touches persistent disk. If it cannot decrypt the
file (wrong or missing age key) or cannot reach OpenBao, it fails safe: OpenBao stays sealed and no
key material is exposed.

This runs on every OpenBao restart — including the routine ones caused by Talos rolling a node —
so unsealing normally needs no operator action once bring-up is done.

## Manual unseal

Only needed if the companion is broken or you are diagnosing it directly. With the plaintext
unseal keys in hand (decrypt the committed file locally with the dedicated age key, or recover
them from wherever they were first recorded):

    export KUBECONFIG=/tmp/rk.yaml
    kubectl -n openbao exec -it deploy/openbao -- bao operator unseal   # repeat M times, one key per invocation
    kubectl -n openbao exec -it deploy/openbao -- bao status            # confirm Sealed: false

Never paste unseal keys or the root token into a file inside this repository, encrypted or not,
outside the one committed `unseal-keys.sops.yaml`.

## Revoke the root token after bootstrap

The root token is only needed for the initial `bao operator init` flow and for re-running
`openbao-config` after a policy change. It should not sit live indefinitely:

    kubectl -n openbao exec -it deploy/openbao -- bao token revoke -self

Re-create `openbao-root-token` (and generate a fresh root token via `bao operator generate-root`
if the CLI is unavailable) only when the bootstrap Job needs to run again — for example, after
editing `infrastructure/openbao-config/eso-read.hcl`.

## How ESO authenticates (Kubernetes auth, role `eso`)

ESO never holds a static OpenBao credential. The `openbao-config` Job configures Kubernetes auth
so that any pod presenting the `external-secrets` ServiceAccount token from the
`external-secrets` namespace can log in as the `eso` role, which is scoped by the `eso-read`
policy to read-only on `secret/data/*` and `secret/metadata/*`. The `ClusterSecretStore` named
`openbao` (`infrastructure/external-secrets-store/clustersecretstore.yaml`) points ESO at
`http://openbao.openbao.svc:8200` with `mountPath: kubernetes` and `role: eso`.

If an `ExternalSecret` reports an auth error, check that:

- `openbao-config` actually ran and completed (`kubectl -n openbao get job openbao-config`) —
  until it has, the `eso` role does not exist yet.
- The `external-secrets` ServiceAccount exists in the `external-secrets` namespace and matches
  `bound_service_account_names`/`bound_service_account_namespaces` in `configure.sh`.
- OpenBao is unsealed (`bao status`) — a sealed OpenBao rejects all API calls, auth included.

## Checking overall health

    export KUBECONFIG=/tmp/rk.yaml
    kubectl -n openbao get pods
    kubectl -n openbao exec -it deploy/openbao -- bao status
    kubectl -n external-secrets get pods
    kubectl get clustersecretstore openbao -o yaml

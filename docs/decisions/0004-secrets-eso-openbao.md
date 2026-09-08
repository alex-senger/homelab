# 4. Self-hosted OpenBao and External Secrets Operator over a SOPS-only path

Date: 2026-09-08

## Status

Accepted

## Context

Sub-project #4 left two secrets created by hand and never committed:
`cloudflare-api-token` (namespace `cert-manager`) and `wg-ingress-key` (namespace `wg-ingress`).
`docs/runbooks/external-reach-recovery.md` calls this out as a knowing suspension of "the whole
cluster is in Git" and defers closing it to this sub-project.

The repository already has a secrets mechanism: SOPS + age, used for the Talos machine secrets in
`talos/talsecret.sops.yaml`. Extending that pattern — committing each application secret
SOPS-encrypted next to the manifest that consumes it — would have closed the gap with no new
components. It was rejected for this use case: it gives no in-cluster read audit trail, no
per-consumer access scoping (any principal that can decrypt one secret can decrypt all of them),
and no place to add secrets that must be generated or rotated without a human editing a Git-tracked
ciphertext file by hand. A vault gives all three, at the cost of running and unsealing one more
stateful component.

Talos's locked-down host networking and immutable filesystem (ADR 0003) rule out anything that
wants to live outside a pod, so whatever holds the values runs in-cluster like everything else
here.

## Decision

**A self-hosted OpenBao (KV v2 engine) plus External Secrets Operator (ESO)**, replacing the
SOPS-only path for values that need audit, scoped access, or operator-driven rotation. OpenBao is
Vault's open-source fork; ESO talks to it over the standard Vault API.

**Single-node, raft storage, on the `longhorn-retain` StorageClass.** ESO-synced Kubernetes
Secrets persist independently of OpenBao once written, so an OpenBao outage pauses new syncs and
refreshes rather than breaking running workloads — the same reasoning as Longhorn's single-replica
posture elsewhere in this cluster. HA would buy little against that failure mode, so one replica is
simplest to run and to unseal. `longhorn-retain` (`reclaimPolicy: Retain`) exists so a PVC delete
never takes the raft data with it.

"Single-node" here refers only to OpenBao running a single application replica; the underlying
`longhorn-retain` volume still keeps `numberOfReplicas: "2"` Longhorn storage replicas, so raft
data itself is not single-copy.

**Auto-unseal via a companion Deployment (`openbao-unsealer`), not a KMS seal.** No cloud KMS is
available here, and Talos reboots every node in turn, so OpenBao restarts — and re-seals — often
enough that manual unsealing would be real toil. The companion decrypts a SOPS-encrypted file of
Shamir unseal keys into a memory-backed `emptyDir` and loops calling the unseal API until OpenBao
reports `sealed=false`; it fails safe; if it cannot decrypt or reach OpenBao, OpenBao stays sealed.

**The age key that decrypts the unseal keys is dedicated to OpenBao, not the Talos master age
key.** This is the one deliberate departure from "reuse the existing SOPS model exactly": Talos's
age key decrypts the cluster's own certificate authorities and bootstrap tokens, so the two must
never share a decryption key. A compromised OpenBao unseal key exposes OpenBao's data; it must
never be able to expose the Talos CAs, and vice versa.

**Engine and auth configuration lives in Git, applied by an idempotent bootstrap Job
(`openbao-config`).** Enabling the KV v2 engine, enabling Kubernetes auth, writing the `eso-read`
policy, and creating the `eso` auth role are all declarative and reconciled like everything else
in this repository — only `bao operator init` and the secret *values* stay manual.

**ESO authenticates to OpenBao via Kubernetes auth (ServiceAccount token), not a static
credential.** The `external-secrets` ServiceAccount in the `external-secrets` namespace is bound to
the `eso` role, scoped by the `eso-read` policy to read-only on the `secret/` KV mount. No token or
password for ESO exists anywhere, in Git or otherwise.

**The two external-reach secrets become OpenBao-backed.** `ExternalSecret`s in `cert-manager` and
`wg-ingress` reference the `openbao` `ClusterSecretStore` and resolve to `secret/cert-manager/cloudflare-api-token`
and `secret/wg-ingress/wg-ingress-key` respectively, closing the gap ADR 0003 left open.

## Consequences

As of this ADR, every manifest above is merged: the OpenBao chart, the unsealer companion, the
bootstrap Job, ESO, the `ClusterSecretStore`, and the two `ExternalSecret`s. **None of it is live
yet.** OpenBao starts uninitialized — `bao operator init` is a one-time step no manifest can
perform, since it is the moment the unseal keys and root token first come into existence.
Until an operator runs it, generates the dedicated age keypair, SOPS-encrypts and commits the real
unseal keys (replacing the placeholder committed in `infrastructure/openbao-unsealer/unseal-keys.sops.yaml`),
creates the `openbao-unseal-age-key` and `openbao-root-token` Secrets, and seeds the two values with
`bao kv put`, OpenBao holds no data and the two `ExternalSecret`s cannot resolve. The bring-up
procedure is `docs/runbooks/openbao-recovery.md`.

**Raft-volume-loss recovery is partial today.** Losing the `openbao` PVC loses all KV data, but
recovery is bounded: the unseal keys are recoverable from the committed SOPS ciphertext plus the
out-of-band age key, and the handful of secret values are small enough to re-seed by hand from
wherever the operator sourced them originally (the Cloudflare dashboard, `wg genkey`). This is
"recoverable with operator effort," not "backed up" — full protection is deferred to whenever this
cluster gets a Longhorn backup target (tracked in the honest-caveats section of the README, not
scoped to this sub-project).

**In-cluster OpenBao TLS is disabled** (`tls_disable = true` in the listener config). Traffic
between OpenBao, its companion, the bootstrap Job, and ESO is plaintext, protected only by staying
inside the cluster network. This matches the "in-cluster only, LAN-verified" trust boundary the
rest of the platform accepts today (ADR 0003's Gateway also terminates TLS only at the edge), but
it is a deferred hardening item, not an oversight: enabling it needs a certificate for the
`openbao.openbao.svc` identity, which is more machinery than this sub-project scoped in.

Dynamic secrets, automatic rotation, HA, and the Longhorn backup target itself are explicitly out
of scope for this sub-project and remain unaddressed by this ADR.

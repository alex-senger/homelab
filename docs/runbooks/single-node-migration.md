# Single-node consolidation — migration runbook

Rebuilds `roastery` from three control-plane VMs to one node `roastery-1` at
192.168.178.16 (control-plane + schedulable, single etcd), Longhorn single-replica
on a ~300 GB NVMe volume. Clean rebuild: ArgoCD replays all apps; OpenBao is
re-seeded. Spec: docs/superpowers/specs/2026-09-10-single-node-consolidation-design.md.

## Before you start — have these in hand

You CANNOT read these back from a wiped cluster:

- **Cloudflare API token** (from your Cloudflare account / records).
- **`wg0.conf`** — the EXISTING WireGuard client config for the tunnel to the VPS
  (re-seeding the same value keeps the VPS peer valid; a new key means re-keying
  the VPS).
- **Grafana admin** user/password (you choose).
- The **dedicated OpenBao unseal age key** (operator-held; the one that decrypts
  `unseal-keys.sops.yaml`) and the **Talos age key** (decrypts `talsecret.sops.yaml`).

## 0. Merge the git changes

Merge the talconfig + Longhorn + monitoring changes to `main` so the rebuilt
cluster's ArgoCD reconciles the single-node config.

## 1. Snapshot for rollback (mandatory)

In Proxmox, snapshot or back up ALL THREE current VMs. This is the only rollback:
if bring-up fails, restore them and the old cluster returns at VIP .19.

## 2. Provision the VM in Proxmox

Create a **fresh VM** `roastery-1` (cleaner than reusing one — the old VMs stay as
rollback). The 32 GB host can't run the old three (~24 GB) plus a new 28 GB VM, so
first **stop** (do not delete) the old VMs to free RAM and the `.16` address; keep
them stopped for rollback and delete only after §6 passes.

New VM settings (Talos v1.13.9 `metal-amd64` ISO):
- Machine **q35**, SCSI controller **VirtIO SCSI single**, CPU type **host** with
  **4 cores**, **28 GB** RAM with **ballooning OFF** (etcd dislikes it).
- **System disk on SCSI (`scsi0` → `/dev/sda`, matches `installDisk`)**, ~50 GB on
  the **NVMe** datastore, Discard + SSD emulation on. (VirtIO Block gives `/dev/vda`
  and breaks the install disk — use SCSI.)
- **Longhorn data disk** `scsi1`, **~300 GB** on the **NVMe** datastore, Discard +
  SSD emulation. (This is the disk the `disk.size > 200u * GB` selector claims.)
- **VirtIO** NIC on the LAN bridge. Leave the HDD out — it's the #7d backup target.

Boot the VM from the ISO; it comes up in **maintenance mode** on a **DHCP** address
(not `.16` yet — the static `.16` applies once the config installs). Note that
DHCP address from the console; call it `MAINT_IP`.

## 3. Provision Talos (single node)

```bash
talhelper genconfig                 # regenerates machineconfig from talconfig + talsecret
# FIRST apply targets the maintenance DHCP IP — the node is NOT at .16 yet:
talosctl apply-config --insecure -n <MAINT_IP> -f clusterconfig/roastery-roastery-1.yaml
# the node installs (with the extension image) and reboots to 192.168.178.16;
# every command after this targets .16:
talosctl --talosconfig clusterconfig/talosconfig config endpoint 192.168.178.16
talosctl --talosconfig clusterconfig/talosconfig config node 192.168.178.16
talosctl bootstrap -n 192.168.178.16
talosctl kubeconfig -n 192.168.178.16 --force     # kubeconfig now points at .16
```

Confirm: `talosctl -n 192.168.178.16 health` and `kubectl get nodes` show one node
(NotReady until Cilium — expected).

## 4. Bootstrap GitOps

```bash
kubectl apply -f cluster/root.yaml
```

ArgoCD installs itself then reconciles every wave (Cilium → gateway/cert-manager →
Longhorn → OpenBao → ESO → monitoring). The node goes Ready once Cilium is up.
OpenBao comes up **uninitialised and sealed** — that is expected.

## 5. Re-seed OpenBao

1. `bao operator init` (via `kubectl exec -n openbao openbao-0 -- bao operator init`)
   — capture the NEW unseal keys + root token.
2. SOPS-encrypt the new unseal keys, overwriting
   `infrastructure/openbao-unsealer/unseal-keys.sops.yaml`, and commit + push (the
   unsealer initContainer needs the current keys):

       cd <repo> && <write plaintext keys to that path> \
         && sops --encrypt --in-place infrastructure/openbao-unsealer/unseal-keys.sops.yaml

3. Recreate the hand-made Secrets:
   - `openbao-unseal-age-key` (the dedicated age private key) in ns `openbao`.
   - `openbao-root-token` in ns `openbao` (for the config Job).

   Then `kubectl -n argocd annotate app openbao-unsealer argocd.argoproj.io/refresh=hard --overwrite`.
4. The `openbao-config` Job (from git) re-enables KV v2, Kubernetes auth, the
   `eso-read` policy and `eso` role. Re-run/verify it is idempotent.
5. `bao kv put` the values (exact paths are in the committed ExternalSecrets —
   `infrastructure/cert-manager-issuers/cloudflare-externalsecret.yaml`,
   `infrastructure/wg-ingress/wg-key-externalsecret.yaml`,
   `infrastructure/monitoring/grafana-externalsecret.yaml`):
   - Cloudflare token → the path/property that ExternalSecret references.
   - `wg-ingress/wg-ingress-key` property `wg0.conf` → the EXISTING wg0.conf.
   - `secret/monitoring/grafana-admin` → `admin-user` + `admin-password`.

## 6. Verify (acceptance)

- `kubectl get nodes` → one `roastery-1` Ready; kubeconfig endpoint is .16.
- `kubectl get applications -n argocd` → all Synced/Healthy.
- Longhorn: `defaultReplicaCount 1`; a PVC binds; data path `/var/mnt/longhorn` on
  the NVMe volume; `kubectl get volumes.longhorn.io -A` healthy.
- OpenBao unsealed; `kubectl get externalsecrets -A` all `SecretSynced`.
- `curl https://whoami.senger-solutions.com` → 200 (external path intact via the
  re-seeded wg0.conf).
- Grafana reachable at its LB IP; node/pod/Hubble metrics flowing.
- Record free RAM (`kubectl top nodes`) — expect materially more headroom than the
  old ~6 GiB/node.

## Rollback

If any step before you are satisfied fails irrecoverably: restore the three VM
snapshots from step 1. The old three-node cluster returns at VIP .19 with its
kubeconfig; nothing in git prevents re-applying the old topology (revert the
single-node commits if already merged).

## Failure notes

- Longhorn PVCs won't bind → check the `longhorn` userVolume mounted at
  `/var/mnt/longhorn` (`talosctl -n .16 get volumestatus`); confirm the 300 GB NVMe
  disk matched the `disk.size > 200u * GB` selector.
- ESO not syncing → OpenBao sealed or a value not `put`; re-check step 5.
- External path down → wrong/absent `wg0.conf`; re-`put` the original value.

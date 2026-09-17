# Single-node consolidation — migration runbook

Rebuilds `roastery` to one node `roastery-1` @ 192.168.178.16 (control-plane + schedulable,
single etcd), Longhorn single-replica on a ~300 GB NVMe volume. Clean rebuild: ArgoCD replays all
apps; OpenBao is re-seeded. Spec: `docs/superpowers/specs/2026-09-10-single-node-consolidation-design.md`.

## Have in hand (can't be read back from a wiped cluster)
Cloudflare API token; the **existing** `wg0.conf` (re-seeding the same value keeps the VPS peer);
Grafana admin user/password; the dedicated OpenBao unseal age key and the Talos age key.

## Steps
1. **Merge** the talconfig + Longhorn + monitoring changes to `main`.
2. **Snapshot all VMs in Proxmox** — the only rollback (restore → old cluster returns at VIP .19).
3. **Provision the VM**: fresh `roastery-1`, Talos v1.13.9 `metal-amd64` ISO. Machine q35, VirtIO
   SCSI single, CPU host 4 cores, 28 GB RAM ballooning OFF. System disk on **SCSI** (`scsi0`→
   `/dev/sda`, matches `installDisk`; VirtIO Block gives `/dev/vda` and breaks it), ~50 GB NVMe.
   Longhorn data disk `scsi1` ~300 GB NVMe (matches `disk.size > 200u*GB`). VirtIO NIC. No HDD (that's
   the #7d target). Stop (don't delete) the old VMs first to free RAM + the `.16` address. Boot →
   maintenance mode on a DHCP IP (`MAINT_IP`).
4. **Provision Talos**:

       talhelper genconfig
       talosctl apply-config --insecure -n <MAINT_IP> -f clusterconfig/roastery-roastery-1.yaml
       # installs (with the extension image) + reboots to .16; everything after targets .16:
       talosctl --talosconfig clusterconfig/talosconfig config endpoint 192.168.178.16
       talosctl --talosconfig clusterconfig/talosconfig config node 192.168.178.16
       talosctl bootstrap -n 192.168.178.16
       talosctl kubeconfig -n 192.168.178.16 --force

5. **Bootstrap GitOps**: `kubectl apply -f cluster/root.yaml` (node Ready once Cilium is up).
6. **Re-seed OpenBao** — see [openbao-recovery.md](openbao-recovery.md): `bao operator init`,
   commit the new SOPS'd unseal keys, recreate `openbao-unseal-age-key` + `openbao-root-token`, run
   `openbao-config`, then `bao kv put` the Cloudflare token, `wg-ingress/wg-ingress-key` (`wg0.conf`
   = the existing file), and `secret/monitoring/grafana-admin`.

## Verify
One `roastery-1` Ready; all ArgoCD apps Synced/Healthy; Longhorn replica-1, PVC binds on
`/var/mnt/longhorn`; OpenBao unsealed + all ExternalSecrets SecretSynced; `curl
https://whoami.senger-solutions.com` → 200 (external path via the re-seeded wg0.conf).

## Rollback
Restore the VM snapshots from step 2; the old cluster returns at VIP .19 (revert the single-node
commits if already merged).

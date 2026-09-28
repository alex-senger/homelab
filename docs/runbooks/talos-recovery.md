# Runbook: Talos node / machine-config recovery

Single node `roastery-1` @ `192.168.178.16` (no VIP).

## Bad machine config
The ISO stays attached — reboot from it to reach maintenance mode (ignores on-disk config, uses
DHCP). Regenerate + apply directly:

    talhelper gencommand apply --extra-flags=--insecure -n roastery-1

`--insecure` because maintenance mode presents no client cert. The static address still comes up
(FritzBox reservation for `.16`) — try the normal address first.

## Inspect without a valid talosconfig
`-i` (insecure) is per-command, *after* the subcommand:

    talosctl get disks -i -n 192.168.178.16 -e 192.168.178.16

Works against a maintenance-mode node or any node whose cert the local talosconfig no longer matches.

## Regenerate machine configs — ALWAYS pass `-s`
    talhelper genconfig -s talos/talsecret.sops.yaml

Without `-s` (e.g. from the repo root) talhelper doesn't fail — it silently mints a **new set of
CAs**, quietly destroying the repo's ability to rebuild the cluster. Verify by diffing the
cluster-CA fingerprint of two outputs:

    talhelper genconfig -s talos/talsecret.sops.yaml -o /tmp/talos-check
    diff <(openssl x509 -noout -fingerprint -in clusterconfig/roastery-roastery-1.yaml 2>/dev/null) \
         <(openssl x509 -noout -fingerprint -in /tmp/talos-check/roastery-roastery-1.yaml 2>/dev/null)

## SOPS can't decrypt (macOS)
SOPS reads `$HOME/Library/Application Support/sops/age/keys.txt`, not `~/.config/sops/...`:

    export SOPS_AGE_KEY_FILE=~/Library/Application\ Support/sops/age/keys.txt

Re-encrypting: `sops -e` matches creation rules on the **output** name → use
`--filename-override talos/talsecret.sops.yaml`.

## Reading a rendered config safely
Never grep with surrounding context — it contains CA private keys. Grep named non-secret keys only
(`hostname`, `installDisk`, `nodeLabels`).

## ISO must match the schematic
Custom Image Factory build with `siderolabs/iscsi-tools` + `util-linux-tools` (Longhorn needs
both: no `iscsid` → no volume attach; no `fstrim` → thin volumes never release blocks). Reinstalling
from a **stock** ISO → volumes won't attach (looks like a storage fault); reapplying the machine
config fixes it (the install image points at the factory build). Schematic
`613e1592b2da41ae5e265e8789429f22e121aab91cb4deb6bc3c0b6262961245`; regen URLs after changing
extensions: `talhelper genurl installer|image -c talos/talconfig.yaml`.

## "certificate signed by unknown authority" from talosctl
Usually a stale `~/.talos/config` from a prior cluster (same context name `roastery`). Compare CA
fingerprints, then install the current one over it (`cp clusterconfig/talosconfig ~/.talos/config`;
regen with `-s` first if `clusterconfig/` is missing).

## Rebuild from scratch
Have in hand first (can't be read back from a wiped cluster): Cloudflare API token, the existing
`wg0.conf` (same value keeps the VPS peer), Grafana admin creds, the OpenBao unseal age key and the
Talos age key.

1. **VM** (Proxmox): q35, VirtIO SCSI single, CPU host 4 cores, 28 GB RAM ballooning OFF, VirtIO
   NIC. System disk ~50 GB NVMe on **SCSI** (`scsi0` → `/dev/sda` = `installDisk`; VirtIO Block
   gives `/dev/vda` and breaks it). Longhorn disk `scsi1` ~300 GB NVMe; garage HDD ~200 GB (see
   backups-recovery.md). Boot the factory ISO → maintenance mode on a DHCP IP (`MAINT_IP`).
2. **Talos**:

       talhelper genconfig -s talos/talsecret.sops.yaml
       talosctl apply-config --insecure -n <MAINT_IP> -f clusterconfig/roastery-roastery-1.yaml
       # installs + reboots to .16; everything after targets .16:
       talosctl --talosconfig clusterconfig/talosconfig config endpoint 192.168.178.16
       talosctl --talosconfig clusterconfig/talosconfig config node 192.168.178.16
       talosctl bootstrap -n 192.168.178.16
       talosctl kubeconfig -n 192.168.178.16 --force

3. **GitOps**: [bootstrap.md](../bootstrap.md).
4. **OpenBao**: re-init and re-seed per [openbao-recovery.md](openbao-recovery.md), then every
   path the ExternalSecrets reference (`git grep -h 'key: ' -- '*externalsecret.yaml'`).

Verify: node Ready; all ArgoCD apps Synced/Healthy; OpenBao unsealed; all ExternalSecrets
`SecretSynced`; `curl https://senger-solutions.com` → 200 (external path via the re-seeded wg0.conf).

## Never
`talosctl bootstrap` runs exactly once per cluster. A second run starts a second etcd → split
brain; the only remedy is resetting the node and rebuilding.

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

## Never
`talosctl bootstrap` runs exactly once, ever. A second run (even on another node) starts a second
etcd → split brain; the only remedy is resetting every node and rebuilding.

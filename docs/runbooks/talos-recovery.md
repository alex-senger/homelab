# Runbook: Talos node or machine-config recovery

## Symptoms

A node stuck `NotReady` with no obvious Kubernetes-side cause, a node unreachable at its
static address after a config change, the VIP not answering, or SOPS refusing to decrypt
`talos/talsecret.sops.yaml`.

## Recovering a node with a bad machine config

The Talos ISO stays attached to every VM, so a node with a broken config can always be
recovered by rebooting it from the ISO. Booting from the ISO reaches maintenance mode, which
ignores whatever is on disk and comes up on DHCP instead. From there, regenerate the config
and apply it directly:

    talhelper gencommand apply --extra-flags=--insecure -n roastery-cp-1

Maintenance mode presents no client certificate — there is nothing yet to authenticate a
normal `talosctl` connection against — which is why `--insecure` is required here and would
not be for a node already running its real config.

## Static addressing did not come up

If a node's static network config is itself the thing that is broken, maintenance mode's
DHCP fallback still finds it: the Fritzbox holds address reservations for `.16`, `.17` and
`.18`, so a node that boots to maintenance mode after an ISO boot is reachable at its usual
address regardless of what its on-disk config says. There is no need to discover a new IP —
try the node's normal address first.

## Inspecting a node without a valid talosconfig

`talosctl --insecure` is a per-command flag, `-i`, and it goes after the subcommand, not
before it:

    talosctl get disks -i -n 192.168.178.16 -e 192.168.178.16
    talosctl version -i -n 192.168.178.16 -e 192.168.178.16

This works against a node in maintenance mode, or any node whose certificate the local
talosconfig no longer matches.

## The VIP does not answer

`192.168.178.19` is a Layer 2 VIP: it is elected among the three control-plane nodes, not
statically assigned to one of them, and the election depends on etcd quorum. If quorum is
lost, nothing holds the VIP and it stops answering ARP for it. Check quorum first, against a
node's own address rather than the VIP:

    talosctl -n 192.168.178.16 etcd members

If quorum is genuinely lost, the VIP will not come back until it is restored. In the
meantime, reach the API server directly on a node that is still up:

    kubectl --server https://192.168.178.16:6443 get nodes

## Regenerating machine configs

    talhelper genconfig -s talos/talsecret.sops.yaml

`talhelper genconfig` resolves its secret file relative to the current working directory,
not to `talconfig.yaml`'s directory. Run it from the repo root without `-s` and it will not
find `talos/talsecret.sops.yaml` — and instead of failing, it prints a warning and silently
mints a brand-new set of CAs. A cluster built from configs generated that way works fine on
its own, but its CAs no longer match what is committed to the repository, which quietly
destroys the repository's ability to rebuild the cluster from source. Always pass
`-s talos/talsecret.sops.yaml` explicitly.

Because the failure is silent, verify it rather than trust it. Generate to a second
directory and compare a fingerprint of the cluster CA between the two outputs:

    talhelper genconfig -s talos/talsecret.sops.yaml -o /tmp/talos-check
    diff <(openssl x509 -noout -fingerprint -in clusterconfig/roastery-cp-1.yaml 2>/dev/null) \
         <(openssl x509 -noout -fingerprint -in /tmp/talos-check/roastery-cp-1.yaml 2>/dev/null)

Identical fingerprints mean `-s` took effect and both runs used the same committed secrets.
Any difference means `-s` is not being honored — do not apply either config until the cause
is found.

## SOPS cannot decrypt the secrets

If `sops -d talos/talsecret.sops.yaml` fails with `no identity matched any of the
recipients`, this reads like the wrong key, but on macOS it is usually the wrong path. SOPS
does not read `~/.config/sops/age/keys.txt` on macOS — it looks in
`$HOME/Library/Application Support/sops/age/keys.txt`. Set `SOPS_AGE_KEY_FILE` explicitly
rather than relying on the default lookup:

    export SOPS_AGE_KEY_FILE=~/Library/Application\ Support/sops/age/keys.txt
    sops -d talos/talsecret.sops.yaml

The reverse direction has its own trap: `sops -e` matches its creation rules against the
input filename, so encrypting a file named `talsecret.yaml` into `talsecret.sops.yaml` fails
with `no matching creation rules found` — the rule matches on the `.sops.yaml` suffix in the
name being written, and `sops -e` never sees that name because it operates on the plaintext
file. Use `--filename-override` to tell it what filename to match rules against:

    sops --filename-override talos/talsecret.sops.yaml -e talos/talsecret.yaml > talos/talsecret.sops.yaml

## Reading a rendered machine config safely

Never grep a rendered machine config for broad context. It contains every CA private key
generated for the cluster, and a context window around an unrelated match will pull one in.
Grep for named, non-secret keys only — `hostname`, `nodeLabels`, `installDisk` and similar —
never an unanchored pattern.

This also matters for finding the VIP. Talos 1.13 renders a multi-document machine config,
and the VIP is not `machine.network.interfaces[].vip` — it is its own document, of kind
`Layer2VIPConfig`. Grepping for a `vip:` key finds nothing in a correctly configured cluster,
and reads exactly like a missing VIP when the config is actually fine. Look for the
`Layer2VIPConfig` document instead of a key.

## Never

Never run `talosctl bootstrap` against more than one node. It initialises etcd, and it is
meant to run exactly once, on exactly one node, ever, for the cluster's whole lifetime.
Running it a second time — even against a different node in the same cluster — starts a
second etcd cluster and produces split brain. The only remedy for that is resetting every
node and starting the cluster over from scratch. There is no node-count check that protects
against this: it is a one-shot command trusted to be run once, by whoever is bootstrapping.

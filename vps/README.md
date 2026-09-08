# VPS: WireGuard tunnel to the cluster

Ansible for the Debian VPS's side of the WireGuard tunnel that lets the cluster's `wg-ingress`
pod reach the outside world. This box is the user's existing production VPS — it already runs a
containerized nginx on `:443` and uses UFW for its firewall. This Ansible does **not** touch
either of those; it manages exactly one thing: the `wg1` WireGuard interface and the single UFW
rule that opens its port.

The cluster dials out to this VPS — the VPS is the WireGuard "server" and listens on UDP
`51821`; it never initiates a connection into the cluster, so no IP forwarding or NAT is
configured here.

Tunnel subnet `10.10.0.0/24`: VPS = `10.10.0.1`, cluster `wg-ingress` pod = `10.10.0.2`.

One role:

- **`wireguard`** — installs WireGuard, templates `/etc/wireguard/wg1.conf`, opens UDP `51821`
  via a single additive `ufw allow` rule (never resets or flushes UFW — that firewall is the
  operator's, shared with everything else on the box), and enables/starts `wg-quick@wg1`.

## nginx routing is not managed here

Routing cluster hostnames over the tunnel to `10.10.0.2:443` is a change the operator makes to
their own, already-running nginx `stream {}` block — this repo does not install, template, or
reload nginx. See `reference/nginx-stream-snippet.conf` for a copy of the SNI-routing snippet
actually in use, kept here only so the mapping is documented.

## Secret hygiene

Nothing in this directory as committed contains the VPS's public IP or any WireGuard key. Two
kinds of real values exist and both live only in untracked files (see `.gitignore` at the repo
root):

- **Connection details** (VPS public IP, SSH user) — copy `inventory.ini` to
  `inventory.local.ini` in this directory and fill in the real values. `inventory.ini` itself
  stays a placeholder example, safe to commit.
- **WireGuard keys** (`wg_vps_private_key`, `wg_cluster_public_key`) — go in an untracked
  `group_vars/vps.yml` in this directory (auto-loaded by Ansible for the `[vps]` group), e.g.:

  ```yaml
  wg_vps_private_key: "<VPS_PRIVATE_KEY>"
  wg_cluster_public_key: "<CLUSTER_PUBLIC_KEY>"
  ```

## Generating the WireGuard keypairs

Two independent keypairs are needed — one per end of the tunnel. Generate each the same way:

```bash
wg genkey | tee privatekey | wg pubkey > publickey
```

- **VPS keypair**: generate this one *on the VPS itself* (or anywhere, then copy the private key
  over securely). The private key goes into `group_vars/vps.yml` as `wg_vps_private_key` and is
  templated into `/etc/wireguard/wg1.conf` on the VPS. The public key is what the cluster side
  needs as *its* peer's key — hand it to whoever configures the in-cluster `wg-ingress` pod's
  peer list.
- **Cluster keypair**: generated separately as part of the cluster-side `wg-ingress` setup. Its
  private key stays in a Kubernetes Secret and never appears here. Its **public** key is what you
  paste into `group_vars/vps.yml` as `wg_cluster_public_key` on this side.

In short: this role only ever holds the VPS's private key and the cluster's public key — never
the cluster's private key, and never the VPS's key on the cluster side. Delete any temporary
`privatekey`/`publickey` files from wherever you ran `wg genkey` once they've been copied into
place; don't leave key material lying around in plaintext longer than necessary.

## Running it

```bash
cd vps
cp inventory.ini inventory.local.ini   # then edit with the real IP and SSH user
$EDITOR group_vars/vps.yml             # create it, with the two keys above

ansible-galaxy collection install community.general
ansible-playbook -i inventory.local.ini playbook.yml
```

`ansible.cfg` in this directory sets sane defaults (`roles_path`, `inventory.local.ini` as the
default inventory) so `-i inventory.local.ini` is mostly a safety-net override if you invoke
`ansible-playbook` from elsewhere.

## Verifying

```bash
# On the VPS:
sudo wg show           # wg1 present, listening :51821, peer configured
sudo ufw status         # udp/51821 allowed, comment "WireGuard - k8s cluster tunnel"
```

No WireGuard handshake will appear until the cluster's `wg-ingress` pod is also up and dialing
out to this VPS — that's expected until both sides exist.

## Firewall

This role uses **UFW**, because that's what the VPS already runs for everything else on the
box — the role must never introduce a second, competing firewall layer (e.g. nftables) that could
conflict with rules the operator manages elsewhere. It adds exactly one rule
(`community.general.ufw`, `rule=allow port=51821 proto=udp`) and never resets, flushes, or
otherwise takes ownership of the ruleset as a whole.

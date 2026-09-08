# VPS: public front door

Ansible for the Debian VPS that terminates the public internet side of external reach:
`internet -> VPS:443 (nginx stream, PROXY protocol) -> WireGuard tunnel -> cluster wg-ingress pod
(10.10.0.2:443) -> Cilium Gateway`. The cluster dials out to this VPS — the VPS is the WireGuard
"server" and listens on UDP `51820`; it never initiates a connection into the cluster.

Tunnel subnet `10.10.0.0/24`: VPS = `10.10.0.1`, cluster `wg-ingress` pod = `10.10.0.2`.

Two roles:

- **`wireguard`** — installs WireGuard, brings up `wg0` at `10.10.0.1/24`, enables IPv4
  forwarding, and opens the ports this box needs (SSH, WireGuard, the nginx listener) via
  `nftables`, with a default-deny inbound policy otherwise.
- **`nginx-stream`** — installs nginx with the stream module and relays every TCP `:443` byte
  over the tunnel to `10.10.0.2:443` with `proxy_protocol on`, so the cluster's Envoy can recover
  the real client address. This box never terminates TLS.

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
  templated into `/etc/wireguard/wg0.conf` on the VPS. The public key is what the cluster side
  needs as *its* peer's key — hand it to whoever configures Task 8 (the `wg-ingress` pod's peer
  list).
- **Cluster keypair**: generated separately as part of Task 8 (the in-cluster `wg-ingress` pod).
  Its private key stays in a Kubernetes Secret and never appears here. Its **public** key is what
  you paste into `group_vars/vps.yml` as `wg_cluster_public_key` on this side.

In short: this role only ever holds the VPS's private key and the cluster's public key — never
the cluster's private key, and never the VPS's key on the cluster side. Delete any temporary
`privatekey`/`publickey` files from wherever you ran `wg genkey` once they've been copied into
place; don't leave key material lying around in plaintext longer than necessary.

## Running it

```bash
cd vps
cp inventory.ini inventory.local.ini   # then edit with the real IP and SSH user
$EDITOR group_vars/vps.yml             # create it, with the two keys above

ansible-playbook -i inventory.local.ini playbook.yml
```

`ansible.cfg` in this directory sets sane defaults (`roles_path`, `inventory.local.ini` as the
default inventory) so `-i inventory.local.ini` is mostly a safety-net override if you invoke
`ansible-playbook` from elsewhere.

## Verifying

```bash
# On the VPS:
sudo wg show                 # wg0 present, listening :51820, peer configured
sudo nginx -t && sudo ss -tlnp | grep :443
```

No WireGuard handshake will appear until the cluster's `wg-ingress` pod (Task 8) is also up and
dialing out to this VPS — that's expected until both sides exist.

## Firewall

This role uses **nftables** (Debian's default nft-based firewall), not `ufw`, because Debian
ships it directly with no extra rule-persistence layer needed — `/etc/nftables.conf` loaded by
`nftables.service` is the whole story. The templated ruleset (see
`roles/wireguard/templates/nftables.conf.j2`) default-denies inbound traffic and explicitly
allows: loopback, established/related connections, ICMP/ICMPv6 essentials, SSH (`ssh_port`,
default `22`), the WireGuard listener (`wg_listen_port`, default `51820`), and the nginx stream
listener (`stream_port`, default `443`). Override `ssh_port` in `group_vars/vps.yml` if the VPS
uses a non-default SSH port — getting this wrong before applying the playbook risks locking
yourself out.

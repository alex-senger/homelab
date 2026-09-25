# VPS (`ristretto`)

Ansible for the public front door: a Debian 13 VPS at IONOS that forwards `:443` (SNI) and
`:25565` over WireGuard (`wg1`, `10.10.0.1` ↔ cluster `wg-ingress` `10.10.0.2`).
Cloudflare proxies all HTTPS; only Cloudflare ranges may reach `:443`.

## Managed

| Role | Does |
|---|---|
| `base` | packages, BBR + network-hardening sysctls |
| `devsec.hardening.ssh_hardening` | full `sshd_config`: keys only, no root, `AllowUsers asg` |
| `firewall` | UFW defaults and every rule below; additive, never resets |
| `robertdebock.fail2ban` | sshd jail (24h ban) |
| `hifis.toolkit.unattended_upgrades` | Debian + security updates |
| `wireguard` | `wg1` on `51821/udp` |
| `nginx_stream` | `nginx.conf` stream proxy (`nginx_stream_*` vars) |

`verify.yml` asserts the end state and runs at the end of `playbook.yml`.

## Not managed (by design)

`wg0` (personal WireGuard, `51820/udp`, its UFW rule and `ip_forward` sysctl), Komodo/periphery,
docker leftovers. UFW is additive so manual rules survive runs.

## Secrets

SOPS + age (recipient in `/.sops.yaml`); decrypted at run time by the `community.sops` vars plugin.

- `group_vars/vps/secrets.sops.yml`: `wg_vps_private_key`, `wg_cluster_public_key`
- `group_vars/vps/ssh_keys.sops.yml`: `asg_ssh_public_keys` (installed by `bootstrap.yml`, exclusive)
- `host_vars/ristretto/secrets.sops.yml`: `ansible_host`, `asg_password_hash`

Edit with `sops edit <file>`.

## Running

```bash
cd vps
ansible-galaxy install -r requirements.yml
ansible-playbook playbook.yml -K --check --diff   # dry run
ansible-playbook playbook.yml -K
ansible-playbook verify.yml -K                    # checks only
```

Tags: `base`, `sshd`, `firewall`, `fail2ban`, `upgrades`, `wireguard`, `nginx`, `verify`.
Keep a second SSH session open when applying `sshd` or `firewall`.

Rebuilding from scratch: [docs/runbooks/vps-recovery.md](../docs/runbooks/vps-recovery.md).

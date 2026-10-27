# VPS (`ristretto`)

Ansible for the website host: a Debian 13 VPS at IONOS that serves `senger-solutions.com` from a
rootless Podman container behind Cloudflare. It has no connection to the cluster, which is
LAN-only.

## Managed

| Role | Does |
|---|---|
| `base` | packages, BBR + network-hardening sysctls |
| `devsec.hardening.ssh_hardening` | full `sshd_config`: keys only, no root, `AllowUsers asg` |
| `firewall` | UFW defaults and every rule below; additive, never resets |
| `robertdebock.fail2ban` | sshd jail (24h ban) |
| `hifis.toolkit.unattended_upgrades` | Debian + security updates |
| `website` | Podman, `website` user, Quadlet unit, pinned image (`website_image`) |
| `nginx` | `:443` http server: Origin CA cert, Authenticated Origin Pulls, → `127.0.0.1:8080` |

`443` accepts only Cloudflare ranges, and nginx only requests carrying this zone's AOP client
certificate. `verify.yml` asserts the end state and runs at the end of `playbook.yml`.

## Not managed (by design)

`wg0` (personal WireGuard, `51820/udp`, its UFW rule and `ip_forward` sysctl), Komodo/periphery,
docker leftovers, the CV PDFs in `/srv/website/downloads`. UFW is additive so manual rules survive
runs.

## Secrets

SOPS + age (recipient in `/.sops.yaml`); decrypted at run time by the `community.sops` vars plugin.

- `group_vars/vps/secrets.sops.yml`: `website_ghcr_username`, `website_ghcr_token`,
  `website_origin_cert`, `website_origin_key`
- `group_vars/vps/ssh_keys.sops.yml`: `asg_ssh_public_keys` (installed by `bootstrap.yml`, exclusive)
- `host_vars/ristretto/secrets.sops.yml`: `ansible_host`, `asg_password_hash`

Edit with `sops edit <file>`.

## Running

```bash
cd vps
ansible-galaxy install -r requirements.yml
ansible-playbook playbook.yml -K --check --diff   # dry run
ansible-playbook playbook.yml -K
ansible-playbook playbook.yml -K --tags website   # deploy a new website image
ansible-playbook verify.yml -K                    # checks only
```

Tags: `base`, `sshd`, `firewall`, `fail2ban`, `upgrades`, `website`, `nginx`, `verify`.
Keep a second SSH session open when applying `sshd` or `firewall`.

Website operations: [docs/runbooks/website-recovery.md](../docs/runbooks/website-recovery.md).
Rebuilding from scratch: [docs/runbooks/vps-recovery.md](../docs/runbooks/vps-recovery.md).

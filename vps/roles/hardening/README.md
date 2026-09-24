# hardening

Reusable baseline hardening for a Debian VPS. sshd and the OS baseline come from the
`devsec.hardening` collection (`ssh_hardening` + `os_hardening`); this role wraps them with
box-safe overrides and adds the pieces devsec doesn't cover — ufw (SSH rate-limit + optional
baseline firewall), CrowdSec (replacing fail2ban), and unattended-upgrades auto-reboot.

Needs the `community.general` and `devsec.hardening` collections
(`ansible-galaxy collection install -r requirements.yml`).

## Usage

**A fresh Debian VPS** (stand up the firewall too):

    ansible-playbook hardening.yml -i "203.0.113.10," -u root -e hardening_manage_ufw=true

**This homelab's VPS** — via `playbook.yml` alongside the `wireguard` role, with
`hardening_manage_ufw` left `false` (ufw is already operator-managed there).

Bring-up is staged and lockout-safe — see
[`docs/runbooks/vps-hardening.md`](../../../docs/runbooks/vps-hardening.md). Don't run all tags
at once against a box you can't console into.

## Tags

| Tag | Runs | Does |
|---|---|---|
| `sshd` | `devsec.hardening.ssh_hardening` | Templates `sshd_config` (modern algos, key-only, no root/forwarding); `ssh_allow_users: asg` |
| `sysctl` | `devsec.hardening.os_hardening` | sysctl + login.defs baseline; intrusive bits (mounts, SUID/SGID, PAM) disabled |
| `upgrades` | `tasks/upgrades.yml` | `unattended-upgrades` + auto-reboot |
| `crowdsec` | `tasks/crowdsec.yml` | CrowdSec + iptables bouncer, collections, admin whitelist, retire fail2ban (gated) |
| `firewall` | `tasks/ufw.yml` | Optional baseline (gated), `ufw limit 22/tcp`, remove superseded plain allow, delete unwanted rules |

## devsec overrides (baked into `tasks/main.yml`)

- `ssh_allow_users: asg`, `ssh_client_hardening: false` (harden the server, not the VPS's own client).
- `sysctl_overwrite: { rp_filter: 2 }` — loose, not devsec's strict `1` (this box carries the WireGuard tunnel).
- `os_users_without_password_ageing: [asg]` — don't expire the key-only admin.
- Intrusive subsystems off: `os_security_suid_sgid_enforce: false`, `os_pam_enabled: false`, all `os_mnt_*_enabled: false`.

## Role variables

- **`hardening_manage_ufw`** (default `false`) — when true, default-deny incoming / allow outgoing and enable ufw (SSH allowed *before* enable, so no lockout). Turn on for a fresh box; leave false where ufw is operator-managed.
- **`hardening_ufw_delete_rules`** (default `[]`) — ufw rules to delete, each `{rule, port, proto}`. Interface-bound rules must be removed by hand.
- **`hardening_admin_whitelist_ips`** (default `[]`) — admin source IPs whitelisted in CrowdSec (parse-time; doesn't filter community-blocklist decisions — clear those with `cscli decisions delete`).
- **`hardening_retire_fail2ban`** (default `false`) — stop + disable fail2ban once CrowdSec is verified.

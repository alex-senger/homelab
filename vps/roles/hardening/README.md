# hardening

Standalone, reusable baseline hardening for a Debian VPS: an sshd drop-in (modern algos,
`MaxAuthTries`, no password/root/forwarding), ufw SSH rate-limiting (plus an optional baseline
firewall for a fresh host), CrowdSec (replacing fail2ban) with the community CTI blocklist, and a
sysctl / unattended-upgrades baseline. Additive by default — it never resets ufw or rewrites main
configs; each task touches only its own drop-in or rule.

## Usage

**A fresh Debian VPS** (stand up the firewall too):

    ansible-galaxy collection install -r requirements.yml
    ansible-playbook hardening.yml -i "203.0.113.10," -u root -e hardening_manage_ufw=true

(see `hardening.inventory.example.ini` for a group-based inventory instead of an inline host.)

**This homelab's VPS** — applied via `playbook.yml` alongside the `wireguard` role, with
`hardening_manage_ufw` left `false` (ufw is already operator-managed there).

Bring-up is staged and lockout-safe — see
[`docs/runbooks/vps-hardening.md`](../../../docs/runbooks/vps-hardening.md) for the order and
per-stage verification. Don't run all tags at once against a box you can't console into.

## Tags

| Tag | Task file | Does |
|---|---|---|
| `sshd` | `tasks/sshd.yml` | Deploys `/etc/ssh/sshd_config.d/10-hardening.conf`, validates with `sshd -t` before reload |
| `sysctl` | `tasks/sysctl_upgrades.yml` | Deploys `/etc/sysctl.d/60-hardening.conf` (rp_filter loose, syncookies, no redirects/source-route) |
| `upgrades` | `tasks/sysctl_upgrades.yml` | Installs `unattended-upgrades`, enables auto-reboot |
| `crowdsec` | `tasks/crowdsec.yml` | CrowdSec + iptables bouncer, collections, admin whitelist, retire fail2ban (gated) |
| `firewall` | `tasks/ufw.yml` | Optional baseline (gated), `ufw limit 22/tcp`, remove superseded plain allow, delete unwanted rules |

## Variables

- **`hardening_manage_ufw`** (default `false`) — when true, sets default-deny incoming / allow
  outgoing and enables ufw (SSH is allowed *before* enable, so no lockout). Turn on for a fresh
  box; leave false where ufw is already operator-managed. Add your own app-port allows separately.
- **`hardening_ufw_delete_rules`** (default `[]`) — ufw rules to delete, each `{rule, port, proto}`
  (e.g. leftovers from a decommissioned service). Interface-bound rules must be removed by hand.
- **`hardening_admin_whitelist_ips`** (default `[]`) — admin source IPs whitelisted in CrowdSec so
  a fat-fingered SSH can't self-ban. This is a parse-time whitelist; it does not filter
  community-blocklist (CAPI) decisions — clear a false-positive with `cscli decisions delete`.
- **`hardening_retire_fail2ban`** (default `false`) — stops + disables fail2ban once CrowdSec is
  verified working.

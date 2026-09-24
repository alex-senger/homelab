# hardening

Additive hardening for the VPS: an sshd drop-in (key algos, `MaxAuthTries`, no
password/root/forwarding), ufw rate-limiting on SSH plus cleanup of stale rules from
decommissioned services, CrowdSec (replacing fail2ban) with the community CTI blocklist, and a
sysctl/unattended-upgrades baseline. Nothing here resets or takes ownership of ufw as a
whole — each task touches only its own rule(s).

Bring-up is staged and lockout-safe — see
[`docs/runbooks/vps-hardening.md`](../../../docs/runbooks/vps-hardening.md) for the order and
per-stage verification. Don't run this role's tags all at once against a box you can't
console into.

## Tags

| Tag | Task file | Does |
|---|---|---|
| `sshd` | `tasks/sshd.yml` | Deploys `/etc/ssh/sshd_config.d/10-hardening.conf`, validates with `sshd -t` before reload |
| `sysctl` | `tasks/sysctl_upgrades.yml` | Deploys `/etc/sysctl.d/60-hardening.conf` |
| `upgrades` | `tasks/sysctl_upgrades.yml` | Installs `unattended-upgrades`, enables auto-reboot |
| `crowdsec` | `tasks/crowdsec.yml` | Installs CrowdSec + iptables bouncer, collections, admin whitelist |
| `firewall` | `tasks/ufw.yml` | `ufw limit 22/tcp`, removes the superseded plain allow, stale-rule cleanup (gated) |

## Toggles

Both default to **`false`** — enable only after verifying the previous stage (see runbook):

- **`hardening_remove_stale_rules`** — deletes the `hardening_stale_ufw_rules` list
  (currently `51820/udp`, `21820/udp` — decommissioned Newt/Pangolin). Run only after
  confirming those services are dead. The `tailscale0`-bound rules (`8120`, `8060`) aren't
  covered — they need a manual `ufw delete allow in on tailscale0 ...` (interface-bound
  deletes aren't expressible via the `community.general.ufw` module).
- **`hardening_retire_fail2ban`** — stops and disables `fail2ban`. Run only after CrowdSec is
  confirmed working (metrics, collections, bouncer chain present).

Also: **`hardening_admin_whitelist_ips`** — list of admin source IPs whitelisted in CrowdSec
so a fat-fingered SSH attempt can't self-ban. Empty by default.

# Runbook: VPS hardening bring-up

Bringing the `hardening` role (`vps/roles/hardening/`) onto the VPS for the first time, or
re-running a stage after a change. Staged, lockout-safe order — each stage verified before the
next. See `vps/roles/hardening/README.md` for the role's toggles and tag scheme.

## Prereqs

    ansible-galaxy collection install -r vps/requirements.yml

- Keep a **second SSH session** open to the VPS for the whole run — don't close your only
  session until each stage is confirmed.
- Confirm the **IONOS web console** is reachable before starting (break-glass path if SSH
  locks you out).

## Staged rollout

Run from `vps/`, one `--tags` group at a time, in this order. Don't skip ahead.

### 1. sysctl + unattended-upgrades

    ansible-playbook playbook.yml --tags sysctl,upgrades

Verify:

    sysctl net.ipv4.tcp_syncookies net.ipv4.conf.all.rp_filter   # expect 1, then 2

- Tunnel, website, and minecraft still reachable (no forwarding/routing changed —
  `rp_filter` stays loose at `2` because this box carries the WireGuard tunnel; `ip_forward`
  is untouched).

### 2. sshd

    ansible-playbook playbook.yml --tags sshd

Verify **from a new terminal**, before closing the old session:

    ssh asg@vps                                              # still works
    ssh -vv asg@vps 2>&1 | grep -iE 'kex|cipher'              # negotiates chacha20/curve25519
    sudo sshd -T | grep -E 'maxauthtries|logingracetime'      # 3, 20

The role runs `sshd -t` on the merged config before reloading — a bad config aborts the play
before the reload handler fires, so this stage can't lock you out via a syntax error. It can
still lock you out via a *valid* config you didn't mean (e.g. wrong key algo) — hence the new
terminal check.

### 3. firewall

    ansible-playbook playbook.yml --tags firewall

Verify:

    sudo ufw status

- Shows `22/tcp LIMIT` (rate-limited: bans a source over 6 connections in 30s).
- **No leftover plain `22/tcp ALLOW`** — the role deletes it so it can't shadow the limit
  rule above (ufw evaluates in order; a plain allow ahead of the limit rule would make the
  limit dead weight).
- Current session survives.

Leave `hardening_ufw_delete_rules` empty for now — rule cleanup is last, after CrowdSec is
verified. `hardening_manage_ufw` stays `false` on this box (ufw is already operator-managed).

### 4. CrowdSec (replaces fail2ban)

    ansible-playbook playbook.yml --tags crowdsec

Verify:

    sudo cscli metrics
    sudo cscli collections list          # sshd, linux, iptables, whitelists
    sudo cscli decisions list
    sudo iptables -S | grep -i crowdsec  # or: sudo nft list ruleset | grep crowdsec

Add your admin IP so a fat-fingered SSH can't self-ban: set `hardening_admin_whitelist_ips`
in `group_vars/vps.yml`, re-run `--tags crowdsec`. Confirm the whitelist parser loaded
(`sudo cscli metrics` shows the `hardening/admin-whitelist` whitelist, or a decision against
your own IP isn't enforced).

Test enforcement against a throwaway IP (not yours):

    sudo cscli decisions add --ip 192.0.2.66 --duration 2m
    sudo iptables -S | grep 192.0.2.66   # or nft equivalent — banned IP in the bouncer set
    sudo cscli decisions delete --ip 192.0.2.66

## After CrowdSec is verified: retire fail2ban

Don't do this until stage 4 above is fully green.

    # set hardening_retire_fail2ban=true in group_vars/vps.yml
    ansible-playbook playbook.yml --tags crowdsec

Verify:

    systemctl is-enabled fail2ban   # disabled

## Stale-rule cleanup (last)

Only once Pangolin/Newt/Tailscale are confirmed truly decommissioned.

    # in group_vars/vps.yml:
    # hardening_ufw_delete_rules:
    #   - { rule: allow, port: "51820", proto: udp }   # Newt (decommissioned)
    #   - { rule: allow, port: "21820", proto: udp }   # Pangolin control (decommissioned)
    ansible-playbook playbook.yml --tags firewall

This removes the listed rules (`51820/udp`, `21820/udp`). The `tailscale0`-bound rules
(`8120`, `8060`) aren't expressible as a plain ufw delete — remove them by hand, v4 and v6:

    sudo ufw delete allow in on tailscale0 to any port 8120
    sudo ufw delete allow in on tailscale0 to any port 8060
    # repeat for any v6 (ip6) entries `ufw status numbered` still shows

Verify:

    sudo ufw status numbered   # no more 51820, 21820, 8120, 8060

## Break-glass

If SSH breaks: use the **IONOS web console** to log in locally, then:

    sudo rm /etc/ssh/sshd_config.d/10-hardening.conf
    sudo systemctl reload ssh

For a firewall lockout, fix or reset the offending `ufw` rule from the console instead.

## Secrets

An optional CrowdSec console enroll key (for the hosted dashboard) goes in the untracked
`vps/group_vars/vps.yml` — not committed. The community CTI blocklist (CAPI) needs no key.

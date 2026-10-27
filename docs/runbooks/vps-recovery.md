# Runbook: VPS rebuild

Rebuilding `ristretto` after a reset or on a new VPS.

1. **Create the VPS** (IONOS): Debian 13, add the controller's SSH key for root.
2. **New IP?** Update `ansible_host` (`sops edit vps/host_vars/ristretto/secrets.sops.yml`) and
   the Cloudflare apex record.
3. **Bootstrap** (creates `asg`):
   ```bash
   cd vps
   ansible-galaxy install -r requirements.yml
   ansible-playbook bootstrap.yml
   ```
4. **Before configuring:** the AOP client cert must already be on the zone (unchanged on a
   rebuild), and the SOPS secrets hold the Origin CA cert and ghcr token.
5. **Configure** (disables root login; verify runs at the end):
   ```bash
   ansible-playbook playbook.yml -K
   ```
6. **Recreate `wg0` by hand** (personal VPN): `/etc/wireguard/wg0.conf`, `ip_forward=1` in
   `/etc/sysctl.d/99-wireguard.conf`, `ufw allow 51820/udp`, `systemctl enable --now wg-quick@wg0`.
7. **Restore the CV PDFs** and check the site; see [website-recovery.md](website-recovery.md).

Lockout: use the IONOS web console.

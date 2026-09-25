# Runbook: VPS rebuild

Rebuilding `ristretto` after a reset or on a new VPS.

1. **Create the VPS** (IONOS): Debian 13, add the controller's SSH key for root.
2. **New IP?** Update `ansible_host` (`sops edit vps/host_vars/ristretto/secrets.sops.yml`), the
   Cloudflare DNS records, and the cluster `wg-ingress` peer endpoint.
3. **Bootstrap** (creates `asg`):
   ```bash
   cd vps
   ansible-galaxy install -r requirements.yml
   ansible-playbook bootstrap.yml
   ```
4. **Configure** (disables root login; verify runs at the end):
   ```bash
   ansible-playbook playbook.yml -K
   ```
5. **Recreate `wg0` by hand** (personal VPN): `/etc/wireguard/wg0.conf`, `ip_forward=1` in
   `/etc/sysctl.d/99-wireguard.conf`, `ufw allow 51820/udp`, `systemctl enable --now wg-quick@wg0`.
6. **Check the tunnel**: `sudo wg show wg1` shows a recent handshake once `wg-ingress` redials;
   see [external-reach-recovery.md](external-reach-recovery.md).

Lockout: use the IONOS web console.

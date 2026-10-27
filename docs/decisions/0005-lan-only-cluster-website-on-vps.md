# 5. LAN-only cluster; website hosted on the VPS

Date: 2026-10-27

## Status

Accepted. Supersedes [0003](0003-external-reach-vps-wireguard.md).

## Context

ADR 0003 gave the cluster public reach through a VPS relaying `:443` over WireGuard. After
Minecraft and Prism were retired, the only public services left were the website, Authelia and
Obsidian LiveSync. Only the website is meant for other people. The tunnel meant a compromised VPS
sat one hop from the home network, with a `NET_ADMIN` pod at the cluster end.

## Decision

**The cluster is LAN-only.** Every service is reached via the Gateway at `192.168.178.3` with
home-DNS overrides; no public DNS records, no tunnel. Certificates still come from Let's Encrypt
via Cloudflare DNS-01, which needs no inbound path.

**The website runs on the VPS**, as a rootless Podman container (Quadlet) deployed by Ansible,
behind Cloudflare. The VPS terminates TLS with a Cloudflare Origin CA certificate and only accepts
requests with this zone's own Authenticated Origin Pulls client certificate.

**Remote access to the cluster is out of scope** here; Tailscale is the likely later answer.

## Consequences

- A compromised VPS exposes a static site and an Origin certificate only Cloudflare trusts; it has
  no route into the home network.
- Cluster services, including LiveSync, work only at home. LiveSync syncs when a device is back on
  the LAN.
- Apps see real client IPs; the VPS relay no longer hides them.
- The website loses ArgoCD reconciliation and Trivy scanning: deploys are manual playbook runs,
  and `verify.yml` flags a merged-but-undeployed image.

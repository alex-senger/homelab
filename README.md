# homelab

GitOps config for `roastery`, a **single-node** Talos Linux Kubernetes cluster on one Proxmox host.
Everything in the cluster is declared here and reconciled by ArgoCD.

## Stack

| Layer | Choice |
|---|---|
| OS | Talos Linux v1.13.9 — immutable, API-managed, no SSH |
| Kubernetes | v1.36.2 |
| CNI | Cilium 1.20.1 — kube-proxy replacement (eBPF), Gateway API, Hubble |
| GitOps | ArgoCD (chart 10.9.1), app-of-apps |
| OS config | talhelper + SOPS-encrypted machine secrets |
| Rendering | Kustomize, Helm charts inflated via `helmCharts:` |
| CI | GitHub Actions — render + `kubeconform -strict` + rendered-object diff per PR; Renovate (automerge gated on CI) |
| Gateway API | Cilium Gateway controller, v1.6.1 experimental CRDs |
| TLS | cert-manager v1.21.2 — Let's Encrypt wildcard via Cloudflare DNS-01 |
| External reach | VPS + WireGuard tunnel → Cilium Gateway (TLS terminates in-cluster) |
| In-cluster DNS | CoreDNS `hosts` override: `*.senger-solutions.com` → the Gateway |
| Secrets | OpenBao (KV v2) + External Secrets Operator |
| Storage | Longhorn (single-replica, NVMe); Garage S3 backups on a separate HDD |
| Database | CloudNativePG (Postgres 17); WAL + PITR to Garage |
| Observability | VictoriaMetrics + Grafana + Hubble |
| SSO | Authelia OIDC + passkeys — ArgoCD, Grafana, Nextcloud behind it |
| Apps | Nextcloud (migrated from AIO) |

## Layout

    bootstrap/        applied by hand once
    cluster/          ArgoCD control plane: root Application, AppProjects, one Application per component
    infrastructure/   platform component manifests
    apps/             user-facing workloads
    talos/            machine config: talconfig, patches, encrypted secrets
    docs/             bootstrap, runbooks, specs

`cluster/applications/` is the map of the cluster — every component and its sync wave in one directory.

## Bootstrap

See [docs/bootstrap.md](docs/bootstrap.md).

## How a change reaches the cluster

Open a PR → CI renders + schema-validates every Kustomization and comments a diff of the **rendered
objects** → merge → ArgoCD reconciles within ~3 min. Rollback is `git revert`.

## Honest caveats

- **Single node, no real HA.** One Talos VM on one Proxmox host (one PSU, one NVMe). Longhorn is
  single-replica — what it buys is surviving Talos-upgrade reboots, not hardware failure. Backups
  land off-NVMe on a separate HDD (Garage).
- **Modest host** (i5-3550S, 4 threads) — scrape intervals and resource requests tuned accordingly.
- **Encrypted secrets, not none.** Talos machine secrets live in `talos/talsecret.sops.yaml` (SOPS);
  the age key is the one thing held out of band. CI refuses tracked `clusterconfig/` or a secrets
  file that loses its SOPS metadata.
- **Cilium CRDs aren't declarative** — the agent/operator register the `cilium.io` CRDs at startup.
- **No client-IP preservation** — the VPS→Gateway hop is L4 SNI passthrough, so apps see the tunnel
  IP (proxy_protocol deferred: it can't coexist with LAN-direct access on the shared Gateway).

## Roadmap — complete

Foundation + ArgoCD + CI (1), declarative Talos (2), Longhorn (3), external reach (4), ESO + OpenBao
secrets (5), observability (6), apps (7: Postgres, Garage backups, Authelia SSO + passkeys,
Nextcloud). Cluster later consolidated from three control-plane VMs to a single node.

## Docs

- [Runbooks](docs/runbooks/) — what to do when it breaks
- Specs under `docs/superpowers/specs/`

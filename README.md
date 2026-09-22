# homelab

> GitOps config for `roastery`: a **single-node** Talos Linux Kubernetes cluster on one Proxmox host.
> Everything in the cluster is declared here and reconciled by ArgoCD.

[![validate](https://github.com/alex-senger/homelab/actions/workflows/validate.yaml/badge.svg)](https://github.com/alex-senger/homelab/actions/workflows/validate.yaml)
[![Renovate](https://img.shields.io/badge/Renovate-enabled-1A1F6C?logo=renovate&logoColor=white)](https://github.com/alex-senger/homelab/issues?q=author%3Aapp%2Frenovate)
![Talos Linux](https://img.shields.io/badge/Talos_Linux-FF7300?logo=talos&logoColor=white)
![Kubernetes](https://img.shields.io/badge/Kubernetes-326CE5?logo=kubernetes&logoColor=white)
![Cilium](https://img.shields.io/badge/Cilium-F8C517?logo=cilium&logoColor=black)
![Argo CD](https://img.shields.io/badge/Argo_CD-EF7B4D?logo=argo&logoColor=white)
![SOPS](https://img.shields.io/badge/secrets-SOPS_encrypted-42484F?logo=gnuprivacyguard&logoColor=white)

## Stack

| Layer | Choice |
|---|---|
| OS | **Talos Linux** |
| Kubernetes | vanilla, kube-proxy-less |
| CNI | **Cilium** |
| GitOps | **ArgoCD**, app-of-apps |
| OS config | **talhelper** + SOPS-encrypted machine secrets |
| Rendering | **Kustomize**, Helm charts inflated via `helmCharts:` |
| CI | **GitHub Actions** — render + `kubeconform -strict` + rendered-object diff per PR; **Renovate** (automerge gated on CI) |
| Gateway API | Cilium Gateway controller, experimental CRDs |
| TLS | **cert-manager** Let's Encrypt wildcard via Cloudflare DNS-01 |
| External reach | **VPS + WireGuard** tunnel → Cilium Gateway (TLS terminates in-cluster) |
| Secrets | **OpenBao** (KV v2) + **External Secrets Operator** |
| Storage | **Longhorn** (single-replica, NVMe); **Garage** S3 backups on a separate HDD |
| Database | **CloudNativePG** (Postgres); WAL + PITR to Garage |
| Observability | **VictoriaMetrics** + **Grafana** + Hubble |
| SSO | **Authelia** |
| Network policy | **Cilium** ingress default-deny on secrets, DB, backups & control-plane |

## Layout

    bootstrap/        applied by hand once
    cluster/          ArgoCD control plane: root Application, AppProjects, one Application per component
    infrastructure/   platform component manifests
    apps/             user-facing workloads
    talos/            machine config: talconfig, patches, encrypted secrets
    docs/             bootstrap, runbooks

## Bootstrap

See **[docs/bootstrap.md](docs/bootstrap.md)**.

## How a change reaches the cluster

Open a PR → merge → ArgoCD reconciles within ~3 min. Rollback is `git revert`.

## Honest caveats

<details>
<summary>It's a learning cluster.</summary>

- **Single node, no real HA.** One Talos VM on one Proxmox host (one PSU, one NVMe). Longhorn is
  single-replica.  What it buys is surviving Talos-upgrade reboots, not hardware failure. Backups
  land off-NVMe on a separate HDD (Garage).
- **Modest host** (i5-3550S, 4 threads). Scrape intervals and resource requests are tuned accordingly.
- **Encrypted secrets, not none.** Talos machine secrets live in `talos/talsecret.sops.yaml` (SOPS);
  the age key is the one thing held out of band. CI refuses tracked `clusterconfig/` or a secrets
  file that loses its SOPS metadata.
- **Cilium CRDs aren't declarative.** The agent/operator register the `cilium.io` CRDs at startup.
- **No client-IP preservation** — the VPS→Gateway hop is L4 SNI passthrough, so apps see the tunnel
  IP (proxy_protocol deferred: it can't coexist with LAN-direct access on the shared Gateway).

</details>

## Docs

- **[Runbooks](docs/runbooks/)** — what to do when it breaks

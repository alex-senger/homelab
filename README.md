# homelab

GitOps configuration for `roastery`, a two-node Talos Linux Kubernetes cluster running on a
single Proxmox host. Everything in the cluster is declared here and reconciled by ArgoCD.

## Stack

| Layer | Choice |
|---|---|
| OS | Talos Linux v1.13.8 — immutable, API-managed, no SSH |
| Kubernetes | v1.36.2 |
| CNI | Cilium 1.18.0, kube-proxy replacement via eBPF, VXLAN, Hubble |
| GitOps | ArgoCD v3.5.1, app-of-apps |
| OS config | talhelper, SOPS-encrypted secrets, three control-plane nodes sharing a VIP |
| Rendering | Kustomize, with Helm charts inflated via `helmCharts:` |
| CI | GitHub Actions — render, `kubeconform -strict`, rendered-object diff on every PR |
| Updates | Renovate, automerge gated on CI |

## Layout

    bootstrap/        applied by hand exactly once
    cluster/          ArgoCD control plane: root Application, AppProjects, one Application per component
    infrastructure/   platform component manifests
    apps/             user-facing workloads
    talos/            machine configuration: talconfig, patches, encrypted secrets
    docs/             bootstrap, runbooks, ADRs, specs and plans

`cluster/applications/` is the map of the cluster: every component and its sync wave, readable
by listing one directory.

## Bootstrap

See [docs/bootstrap.md](docs/bootstrap.md). Two commands.

## How a change reaches the cluster

1. Open a pull request.
2. CI renders every Kustomization, schema-validates it, and comments with a diff of the
   **rendered Kubernetes objects** — not a values diff.
3. Merge. ArgoCD reconciles within about three minutes.

Rollback is `git revert`.

## Honest caveats

This is a learning cluster, and this README should say what it is rather than what it resembles.

**High availability is practised here, not achieved.** Both nodes are VMs on one Proxmox host
with one power supply. Replicated storage would place every replica on the same NVMe. What
replication genuinely buys is surviving *node* reboots, which matters because Talos upgrades are
frequent — not surviving hardware failure. The mitigation that does help is placing the storage
backup target on a physically separate SATA disk.

**The host is modest.** An Intel i5-3550S: four threads shared between the Talos nodes and a
Debian VM. It works. It is not fast, and Prometheus scrape intervals are tuned accordingly.

**One control-plane node.** etcd has no quorum, so a control-plane reboot is an API outage. A
third node is planned.

**Cilium's CRDs are not declarative.** The Cilium chart ships no CustomResourceDefinition
manifests; the agent and operator register the ten `cilium.io` CRDs programmatically at startup.
Everything else about the CNI is declared here, but those ten resources exist because Cilium put
them there, not because this repository asked for them.

**The repository holds encrypted secrets, not none.** The Talos machine secrets — the cluster
certificate authorities and bootstrap tokens — live here in `talos/talsecret.sops.yaml`, encrypted
with SOPS. The age private key is the one thing held out of band. The property is therefore "no
plaintext secrets, one key outside the repository", which is the standard arrangement but weaker
than "no secrets at all", and the distinction is worth stating rather than glossing.

CI enforces it: a job refuses to pass if `clusterconfig/` is ever tracked, or if the committed
secrets file loses its SOPS metadata or gains a plaintext key block.

## Design documents

- [Specs](docs/superpowers/specs/) — what is being built and why
- [ADRs](docs/decisions/) — decisions and their consequences
- [Runbooks](docs/runbooks/) — what to do when it breaks

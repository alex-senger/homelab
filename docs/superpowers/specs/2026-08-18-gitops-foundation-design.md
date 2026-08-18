# Design: GitOps Foundation for the `roastery` Cluster

**Date:** 2026-08-18
**Status:** Approved
**Scope:** Sub-project #1 of 6 — repository foundation, ArgoCD bootstrap, adoption of
already-running components, CI validation, and dependency automation.

---

## 1. Goal

Build a GitOps repository for a Talos Linux Kubernetes homelab that is good enough to put on a
CV. Every piece of cluster configuration lives in Git; ArgoCD reconciles it; changes arrive
through pull requests that show the reviewer exactly which Kubernetes objects will change.

Two goals sit behind that, and they occasionally conflict:

- **Learning.** Prefer designs that expose how Kubernetes works over designs that hide it.
- **Legibility.** A reader should understand the whole cluster from the repository alone.

Where they conflict, legibility wins. A clever mechanism nobody can follow is worth less than an
explicit one they can.

## 2. Current state

Cluster `roastery`, Talos v1.13.8, Kubernetes v1.36.2, on a single Proxmox host.

| Node | Address | Role | Resources |
|---|---|---|---|
| `talos-hzi-iqa` | 192.168.178.16 | control-plane, tainted `NoSchedule` | 2 vCPU, 4 GB, 34 GB disk |
| `talos-zav-s8r` | 192.168.178.17 | worker | 2 vCPU, 4 GB, 34 GB disk |

Installed by hand, present only as cluster state:

- **Cilium 1.18.0**, a real Helm release in `kube-system`. kube-proxy replacement, VXLAN tunnel
  routing, `ipam: kubernetes`, Hubble enabled, LB-IPAM enabled.
- **ArgoCD v3.5.1**, applied from raw upstream manifests. No Helm release object exists.
- **metrics-server** and **kubelet-serving-cert-approver**.

Absent: any StorageClass or PersistentVolume, any `CiliumLoadBalancerIPPool`, Gateway API,
cert-manager, external-dns, and any secret management. L2 neighbour discovery is off, so a
`type: LoadBalancer` Service would sit in `<pending>` indefinitely.

`~/workspaces/homelab` contains a single `README.md` on one commit, pushed to
`github.com/alex-senger/homelab`.

## 3. Constraints

**The Proxmox host is the binding constraint.** Fujitsu ESPRIMO C910-L, Intel i5-3550S
(4 cores / 4 threads, Ivy Bridge), 16 GB DDR3-1600 with two free DIMM slots, 1 TB SATA HDD
(Proxmox OS) plus 1 TB NVMe. It also runs a Debian VM (2 vCPU, 5 GB RAM, 100 GB + 500 GB) hosting
the current docker-compose services.

Allocated today: 5 GB (Debian) + 4 GB + 4 GB (Talos) = 13 GB, plus roughly 1–1.5 GB of Proxmox
overhead. The worker node already reports ~1630 MiB in use with nothing deployed — that is the
Talos + Cilium + kubelet floor, about 1.6 GB per node.

The full intended stack costs roughly:

| Component | RAM |
|---|---|
| Node baselines, 3 × 1.6 GB | ~4.8 GB |
| Longhorn (manager + engines) | ~1.5–2 GB |
| kube-prometheus-stack + Loki | ~2–2.5 GB |
| OpenBao + External Secrets Operator | ~0.4 GB |
| Nextcloud + Postgres + Redis | ~1.5 GB |
| Authelia + website + ArgoCD | ~1 GB |
| **Total** | **~11.5 GB** |

With the Debian VM and host overhead that is ~18 GB against 16 GB installed. **Resolution:
add 2 × 8 GB DDR3-1600 to reach 32 GB** (~€25 used; the i5-3550S supports 32 GB). This keeps the
Debian VM alive and leaves headroom. It is a prerequisite for sub-projects #3 onward, not for #1.

Two constraints that will not be engineered away, and are documented rather than hidden:

- **CPU is the long-term ceiling.** Four threads shared by three Talos VMs and the Debian VM,
  with Longhorn replicating over VXLAN between VMs on the same physical host. It will work; it
  will not be fast. Prometheus scrape intervals and Longhorn replica counts are the knobs.
- **Replicated storage on one physical host is not redundancy.** All Longhorn replicas land on the
  same NVMe. What replication actually buys here is surviving *node* reboots, which matters
  because Talos upgrades will be frequent. The README states this plainly. It is partially
  mitigated by putting the Longhorn backup target on the separate SATA HDD — see §18.

## 4. Exposure model

No router port-forwarding (deliberate, on security grounds) and no Cloudflare Tunnel
(insufficient throughput for Nextcloud). The existing pattern is: rented VPS running nginx →
Tailscale → Pangolin reverse proxy on the Debian VM → docker-compose services.

The cluster follows the same shape but stands alone, because whether the Debian VM is ever
decommissioned is undecided. An **in-cluster Tailscale subnet router** advertises the Cilium
LoadBalancer IP range onto the tailnet; the VPS nginx forwards to a stable LoadBalancer IP.
Inside the cluster, traffic is handled by ordinary Gateway API and cert-manager.

This keeps the interesting parts — LB-IPAM, L2 announcements, Gateway API, certificate issuance —
as portable Kubernetes configuration rather than delegating them to a vendor operator. Detailed
design belongs to sub-project #3.

## 5. Decomposition

The full ambition spans six independent subsystems. Designing them in one document produces
something nobody can implement. Each gets its own spec → plan → implementation cycle.

| # | Sub-project | Depends on |
|---|---|---|
| **1** | **Repo foundation** — layout, ArgoCD bootstrap, adopt Cilium + ArgoCD, CI, Renovate | nothing |
| 2 | Talos layer in Git — talhelper, third node, RAM upgrade, untaint control plane | RAM |
| 3 | Platform — Longhorn, LB-IPAM + L2 announcements, Gateway API, cert-manager, Tailscale subnet router | 1, 2 |
| 4 | Secrets — OpenBao + External Secrets Operator | 3 |
| 5 | Observability — kube-prometheus-stack, Loki, Hubble metrics | 3, 4 |
| 6 | Applications — personal website → Authelia → Nextcloud | 3, 4 |

**This document specifies sub-project #1 only.** It is first because it is the stated open
problem, it unblocks everything else, and it needs no new hardware.

## 6. Decisions

| Decision | Choice | Why |
|---|---|---|
| Delivery tool | ArgoCD (already installed) | Already running; strong UI for learning; app-of-apps is explicit |
| Application declaration | Explicit app-of-apps, one `Application` per component | Sync waves, projects and per-app overrides are one readable field; ApplicationSet generators would rebuild this with more indirection |
| Rendering | Kustomize everywhere, Helm charts inflated via `helmCharts:` | CI renders byte-for-byte what the repo-server renders; chart output stays patchable |
| Cluster/env layout | Flat, single cluster | One cluster exists. Promotion structure is YAGNI and can be introduced later |
| Secrets | External Secrets Operator + self-hosted OpenBao | Production-shaped; Vault-compatible learning; fully FOSS licensing |
| OS layer | Talos machine configs in Git via talhelper | Whole stack reproducible from bare VM upward; most homelab repos stop at Kubernetes |
| CI | GitHub Actions: render → kubeconform → PR object diff | Validates real output rather than approximating it |
| Dependency updates | Renovate GitHub App | Free for public repos, nothing to self-host, no Actions minutes |
| Resource tracking | `annotation` rather than the default `label` | Default stamps `app.kubernetes.io/instance` onto managed resources, colliding with labels charts already set |

Rejected alternatives, recorded so they are not relitigated:

- **ApplicationSet auto-discovery.** Zero boilerplate, but sync ordering and per-app overrides
  require a second config-file generator layer, and failures mean debugging a generator instead of
  reading a file.
- **Multi-source Applications with Helm-native `$values`.** Idiomatic for pure Helm, but CI must
  reimplement the render with `helm template` and hope it matches, and any resource the chart does
  not ship requires a third source anyway.

## 7. Repository layout

```
homelab/
├── README.md                     # architecture, bootstrap, honest caveats
├── .github/
│   ├── workflows/validate.yaml
│   └── renovate.json5
├── docs/
│   ├── bootstrap.md
│   ├── runbooks/                 # recovery procedures
│   ├── decisions/                # ADRs
│   └── superpowers/specs/        # this document
├── bootstrap/                    # hand-applied exactly once
│   └── argocd/kustomization.yaml #   resources: [../../infrastructure/argocd]
├── cluster/                      # the ArgoCD control plane
│   ├── root.yaml                 #   root Application → cluster/
│   ├── kustomization.yaml
│   ├── projects/                 #   AppProjects: infrastructure, apps
│   └── applications/             #   one Application per component, with syncWave
│       ├── cilium.yaml
│       └── argocd.yaml
├── infrastructure/               # platform component manifests
│   ├── cilium/{kustomization.yaml,values.yaml}
│   └── argocd/{kustomization.yaml,values.yaml}
├── apps/                         # empty until sub-project #6
└── talos/                        # empty until sub-project #2
```

Four layers, each with one responsibility:

- `bootstrap/` is the only thing a human ever applies.
- `cluster/` declares **what runs and in what order**. Every `Application` sits in one directory,
  so the deployment order of the entire cluster is readable top-to-bottom without opening a
  component.
- `infrastructure/` and `apps/` declare **how each component is configured**, and contain no
  knowledge of ArgoCD.
- `talos/` declares the OS layer (sub-project #2).

### The bootstrap indirection is deliberate

`bootstrap/argocd/kustomization.yaml` contains nothing but:

```yaml
resources:
  - ../../infrastructure/argocd
```

There is therefore exactly one definition of ArgoCD in the repository. The usual failure of
self-managing ArgoCD — a bootstrap manifest that drifts from the GitOps-managed one, leaving the
application permanently `OutOfSync` — is structurally impossible.

## 8. Rendering model

Every leaf directory is a Kustomization. Upstream Helm charts are inflated in place:

```yaml
helmCharts:
  - name: cilium
    repo: https://helm.cilium.io
    version: 1.18.0
    releaseName: cilium
    namespace: kube-system
    valuesFile: values.yaml
```

`kustomize build --enable-helm infrastructure/cilium` on a laptop produces exactly what the
ArgoCD repo-server produces. This requires `kustomize.buildOptions: --enable-helm` in
`argocd-cm`, set through `infrastructure/argocd/values.yaml`.

Renovate's `kustomize` manager reads `helmCharts[].version` natively, so chart pins are kept
current without custom regex managers.

## 9. ArgoCD control plane

**AppProjects, with real boundaries:**

- `infrastructure` — may create cluster-scoped resources (CRDs, ClusterRoles,
  CiliumNetworkPolicies) in any namespace. Source repositories: this repo plus the upstream Helm
  repositories it inflates.
- `apps` — may **not** create cluster-scoped resources, and is whitelisted to its own namespaces.
  When Nextcloud arrives, a bad chart cannot quietly grant itself cluster-admin.

**Sync waves**, set on the Applications in `cluster/applications/`:

| Wave | Contents |
|---|---|
| -2 | Reserved for namespaces and CRDs. Unused in sub-project #1: `kube-system` already exists, and the `argocd` namespace is created by `bootstrap/` |
| -1 | Cilium — nothing schedules without a CNI |
| 0 | ArgoCD itself |
| 10+ | Platform services (sub-projects #3–#5) |
| 20+ | Applications (sub-project #6) |

**Root Application** (`cluster/root.yaml`) targets `cluster/` with automated sync, self-heal, and
prune enabled. Pruning is safe at this level because the root application manages only
`Application` and `AppProject` resources.

**ArgoCD's own Application** (`cluster/applications/argocd.yaml`) also runs automated sync with
`selfHeal: true`. This is what the drift test in §15 exercises. Cilium is the deliberate
exception (§11).

**Finalizers are applied selectively.** `resources-finalizer.argocd.argoproj.io` on an
`Application` cascade-deletes its managed resources. Cilium's Application does **not** get one:
deleting it must not delete the CNI.

## 10. Adopting ArgoCD: reinstall, do not adopt

ArgoCD was installed from raw `install.yaml`. The Helm chart sets a different `spec.selector` on
its Deployments — it adds `app.kubernetes.io/instance`. `spec.selector` is immutable, so a
chart-based sync over the existing Deployments fails outright. Adoption is not possible.

The resolution is to delete the `argocd` namespace and reinstall from the repository. The only
ArgoCD state in the cluster is the auto-created `default` AppProject, so this costs nothing now
and will only get more expensive later.

Because the namespace is deleted, `infrastructure/argocd/` must itself contain an explicit
`Namespace: argocd` resource alongside the inflated chart, with `namespace: argocd` set on the
Kustomization. `bootstrap/` therefore creates the namespace it installs into, and the ArgoCD
Application later reconciles that same Namespace object rather than relying on
`CreateNamespace=true`.

Values set in `infrastructure/argocd/values.yaml`:

- Dex disabled and the notifications controller disabled — roughly 100 MB reclaimed on a 4 GB
  node. Dex returns in sub-project #6, when Authelia becomes the OIDC provider.
- `configs.cm."kustomize.buildOptions": --enable-helm`
- `configs.cm."application.resourceTrackingMethod": annotation`
- `configs.params."server.insecure": true` — TLS terminates at the Gateway from sub-project #3;
  until then access is via `kubectl port-forward`.
- Modest resource requests and limits appropriate to the node size.

The chart version is pinned to whichever release ships appVersion v3.5.x, resolved at
implementation time.

## 11. Adopting Cilium: in place, behind a diff gate

Cilium is a genuine Helm release and its values are Talos-specific. Getting them wrong takes down
cluster networking. Captured verbatim from the live release:

```yaml
cgroup:
  autoMount:
    enabled: false
  hostRoot: /sys/fs/cgroup
ipam:
  mode: kubernetes
k8sServiceHost: localhost      # Talos KubePrism
k8sServicePort: 7445
kubeProxyReplacement: true
securityContext:
  capabilities:
    ciliumAgent:
      [CHOWN, KILL, NET_ADMIN, NET_RAW, IPC_LOCK, SYS_ADMIN, SYS_RESOURCE,
       DAC_OVERRIDE, FOWNER, SETGID, SETUID]
    cleanCiliumState: [NET_ADMIN, SYS_ADMIN, SYS_RESOURCE]
```

These go into `infrastructure/cilium/values.yaml` unchanged.

**The gate.** Before ArgoCD is allowed near Cilium, the rendered output must match the live
manifest:

```bash
kustomize build --enable-helm infrastructure/cilium > /tmp/rendered.yaml
helm get manifest cilium -n kube-system > /tmp/live.yaml
dyff between /tmp/live.yaml /tmp/rendered.yaml
```

Only when that diff is empty is Cilium's `Application` created — with manual `syncPolicy`,
`prune: false`, `selfHeal: false`, and no finalizer. Automated sync is enabled in a **later,
separate commit**, once a manual sync has returned clean.

Afterwards the stale `sh.helm.release.v1.cilium.v1` secret in `kube-system` is deleted so that
only one source of truth remains. `helm list -A` returning empty is part of the definition of
done.

**Why recovery is always possible.** The kube-apiserver runs on host networking at
`192.168.178.16:6443` and stays reachable with Cilium fully broken, so
`helm upgrade cilium cilium/cilium --version 1.18.0 -f values.yaml` from a laptop always works.
`talosctl` does not depend on the CNI either. Both paths are written into `docs/runbooks/`.

## 12. Bootstrap sequence

Documented in `docs/bootstrap.md`. On a clean cluster, two commands:

```bash
kubectl apply -k bootstrap/argocd
kubectl apply -f cluster/root.yaml
```

For the one-time migration off the hand-installed ArgoCD, the namespace deletion from §10 comes
first:

```bash
kubectl delete namespace argocd
kubectl apply -k bootstrap/argocd
kubectl apply -f cluster/root.yaml
```

The Argo CRDs are cluster-scoped and survive the namespace deletion; the `default` AppProject is
recreated automatically. Subsequent rebuilds need only the two-command form.

The root Application syncs, AppProjects appear, every other Application appears, and
`infrastructure/argocd` adopts the installation that just created it.

ArgoCD becomes self-managing **before** Cilium is adopted. If the Cilium step goes wrong, the
GitOps machinery is already proven, so only one thing is being debugged.

## 13. CI

`.github/workflows/validate.yaml`, on `pull_request` and `push` to `main`. Three jobs.

**`prepare`** discovers every Kustomization and emits a JSON matrix:

```bash
find . -name kustomization.yaml -printf '%h\n' | jq -Rnc '[inputs] | {dir: .}'
```

No hardcoded list to forget. A new component directory is validated on the same pull request that
introduces it.

**`validate`** fans out over that matrix:

```bash
kustomize build --enable-helm "$DIR" \
  | kubeconform -strict -summary \
      -schema-location default \
      -schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'
```

`-strict` rejects unknown fields, catching the mistyped Helm value that would otherwise silently
do nothing. The CRDs-catalog schema location covers `cilium.io` and `argoproj.io`, so custom
resources are validated alongside built-in kinds. `~/.cache/helm` is cached so chart pulls do not
dominate runtime.

**`diff`** renders every Kustomization at the base ref and the head ref, runs `dyff` between them,
and posts a sticky pull-request comment. The reviewer sees the actual Kubernetes objects that
will change rather than a values diff they must mentally compile. Requires
`permissions: {contents: read, pull-requests: write}`.

**`lint`** runs kube-linter as a **non-blocking** job — missing resource limits, missing probes,
running as root. Advisory, because upstream charts will trip it and a permanently red check is a
check nobody reads.

## 14. Renovate

Renovate GitHub App, configured by `.github/renovate.json5`:

- `extends: ["config:recommended", ":dependencyDashboard", "helpers:pinGitHubActionDigests"]`
- `timezone: "Europe/Berlin"`, weekly schedule, so the repository does not read as bot spam
- Cilium and ArgoCD are **never** automerged and always get their own pull request; either can
  take the cluster down
- Patch-level updates elsewhere automerge via `platformAutomerge: true`, gated on CI passing
- Major updates always separate
- Custom regex managers for Talos and talhelper versions arrive with sub-project #2

## 15. Verification

Nothing here is unit-testable, so verification is behavioural.

| # | Check | Frequency |
|---|---|---|
| 1 | CI green on a pull request, with a rendered diff comment visible | every PR |
| 2 | Cilium render-vs-live parity diff empty (§11) | once, gating |
| 3 | Every Application `Synced` and `Healthy`; `cilium status --wait` clean; no `CrashLoopBackOff` in `kubectl get pods -A` | after bootstrap |
| 4 | **Drift test:** `kubectl scale deploy/argocd-repo-server --replicas=2`, confirm self-heal reverts it | once, then after major changes |
| 5 | `helm list -A` returns empty | once |

Check 4 is the only one that proves GitOps is *working* rather than merely installed. It is also
the obvious thing to record as a GIF for the README.

**Definition of done.** A fresh `git clone` plus the two bootstrap commands rebuilds the ArgoCD
layer. Cilium and ArgoCD are both Synced, Healthy, and Git-managed. CI is green with a diff
comment. The Renovate dependency dashboard is open with at least one pull request. The README
explains the architecture including the single-host-HA caveat.

## 16. Failure modes

| Failure | Detection | Recovery |
|---|---|---|
| Cilium synced with wrong values; pod networking dies | `cilium status`, CoreDNS failures | `helm upgrade cilium cilium/cilium --version 1.18.0 -f values.yaml` from laptop; apiserver stays reachable on host networking |
| Cilium Application deleted, cascading to the CNI | Cluster-wide pod failures | Prevented by design: no finalizer on that Application |
| ArgoCD breaks its own deployment | Applications stop reconciling | `kubectl apply -k bootstrap/argocd` re-applies from Git |
| ArgoCD permanently `OutOfSync` against itself | Application health | Prevented by design: `bootstrap/` is a one-line reference to `infrastructure/argocd` |
| Chart render drifts from live state | CI `diff` job on the PR | Review before merge |
| Renovate merges a breaking CNI update | Cluster degradation | Prevented by design: Cilium and ArgoCD never automerge |

## 17. Implementation order

Each step is one or more Conventional Commits. Order matters — later steps depend on earlier ones
being verified.

1. `chore:` repository skeleton, directory layout, `.gitignore`, ADR scaffolding
2. `docs:` README architecture section, `docs/bootstrap.md`, `docs/runbooks/`
3. `feat:` `infrastructure/cilium` with values captured from the live release; run the parity diff
   gate and record the result
4. `feat:` `infrastructure/argocd` values and Kustomization
5. `feat:` `bootstrap/argocd` one-line indirection
6. `feat:` `cluster/` — AppProjects, root Application, Application for ArgoCD
7. `feat:` delete the `argocd` namespace, reinstall from the repository, apply the root
   Application; verify self-management and run the drift test
8. `feat:` Cilium Application with manual sync and no finalizer; manual sync; verify
9. `feat:` enable automated sync for Cilium; delete the stale Helm release secret
10. `ci:` `validate.yaml` — prepare, validate, diff, lint jobs
11. `ci:` `renovate.json5` and Renovate App onboarding
12. `docs:` README caveats, drift-test GIF, badges

## 18. Storage topology

Confirmed 2026-08-18 via `pvesm status`:

| Proxmox storage | Backing device | Total | Used | Available |
|---|---|---|---|---|
| `local` (dir) | SATA HDD | 94 GiB | 6.9 GiB | 82 GiB |
| `local-lvm` (lvmthin) | SATA HDD | 794 GiB | 276 GiB | 518 GiB |
| `nvme` (lvmthin) | NVMe | 931 GiB | 97.5 GiB | 834 GiB |

The 276 GiB written on `local-lvm` is the Debian VM's 500 GB data disk, thin-provisioned. The
97.5 GiB on `nvme` accounts for Debian's 100 GB boot disk plus both 34 GB Talos system disks —
so the NVMe currently carries **168 GiB provisioned against a 931 GiB pool**.

Three Talos nodes with useful data disks therefore fit comfortably:

| Allocation | Size |
|---|---|
| Debian boot (existing) | 100 GiB |
| Talos system disks, 3 × 34 GiB | 102 GiB |
| Talos data disks for Longhorn, 3 × 100 GiB | 300 GiB |
| **Provisioned on `nvme`** | **502 GiB of 931 GiB** |

No thin overprovisioning, so no pool-exhaustion risk.

**Two storage tiers, not one.** Two independent physical devices with different characteristics,
and 518 GiB spare on the HDD, argue for splitting by workload rather than pooling everything:

- **NVMe / Longhorn** — latency-sensitive volumes: Postgres, OpenBao, Prometheus TSDB, Redis.
- **HDD / bulk tier** — Nextcloud file data and the **Longhorn backup target**, where sequential
  throughput matters and IOPS do not.

The backup target placement is the significant part. A Longhorn backup target on a *physically
different device* is the only element of this design that protects against NVMe failure. It
converts the §3 caveat — replication across VMs on one host is not redundancy — from a flat
limitation into a documented, mitigated risk. Detailed design belongs to sub-project #3.

## 19. Open items

- **ArgoCD chart version** for appVersion v3.5.x, resolved at implementation time.
- **Third Talos node system disk size.** The two existing nodes use 34 GiB. Talos grows its
  `EPHEMERAL` partition automatically when the underlying disk grows, and that partition holds
  container images and etcd. 34 GiB is workable but tight once Nextcloud, Prometheus and Longhorn
  images are cached; ~60 GiB is the safer size for the new node, with the existing two grown to
  match. Decided in sub-project #2.

### Resolved

- ~~**Verify NVMe capacity.**~~ Resolved 2026-08-18 — see §18. The assumption held: Debian's
  500 GB data disk is on the SATA HDD, not the NVMe.
- ~~**Control-plane taint.**~~ Resolved 2026-08-18 — the `node-role.kubernetes.io/control-plane`
  `NoSchedule` taint has been removed from `talos-hzi-iqa`, so both nodes are schedulable and
  workloads spread across ~8 GB rather than 4 GB. Sub-project #2 must persist this in the Talos
  machine config (`cluster.allowSchedulingOnControlPlanes: true`) so it survives a node rebuild;
  until then it exists only as live cluster state.

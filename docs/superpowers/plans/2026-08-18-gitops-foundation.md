# GitOps Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put the `roastery` Talos cluster's existing Cilium and ArgoCD installations under GitOps control in a repository structured for growth, with CI that validates rendered Kubernetes objects and Renovate keeping dependencies current.

**Architecture:** ArgoCD app-of-apps. A hand-applied root `Application` points at `cluster/`, which holds one explicit `Application` per component with explicit sync waves. Every leaf directory is a Kustomization; upstream Helm charts are inflated in place via `helmCharts:`, so `kustomize build --enable-helm` locally, in CI, and in the ArgoCD repo-server all produce identical output.

**Tech Stack:** Talos Linux v1.13.8, Kubernetes v1.36.2, Cilium 1.18.0, ArgoCD v3.5.x (Helm chart), Kustomize 5.8.1, kubeconform, dyff, GitHub Actions, Renovate.

**Spec:** `docs/superpowers/specs/2026-08-18-gitops-foundation-design.md`

---

## Orientation for the implementer

You are working in `~/workspaces/homelab`, a git repository whose remote is `git@github.com:alex-senger/homelab.git`. It currently contains a `README.md`, a design spec, and this plan.

**This is not an application codebase.** There is no compiler and no unit-test runner. The equivalent of a failing test is one of two things, and every task uses one of them:

1. **`kustomize build --enable-helm <dir> | kubeconform -strict`** — proves the YAML renders and every field is real. `-strict` rejects unknown fields, which is what catches a mistyped Helm value that would otherwise silently do nothing.
2. **A parity diff** — `dyff between <live-manifest> <rendered-manifest>` — proves that what the repository will apply is byte-for-byte what is already running. This is the gate that prevents adopting Cilium from taking the cluster's networking down.

**Why every kubeconform invocation carries `-skip CustomResourceDefinition`:** verified 2026-08-18, the `kubernetes-json-schema` catalog that `-schema-location default` resolves to publishes no schema for the `CustomResourceDefinition` kind itself, under any path or version. It serves `deployment-apps-v1.json` but returns 404 for `customresourcedefinition-apiextensions-v1.json`. Without the skip, any chart shipping CRDs — ArgoCD ships three — fails validation permanently. `-skip` is used rather than `-ignore-missing-schemas` because the latter silently waves through *every* unrecognised kind, which would defeat the point of `-strict` for the custom resources this repository actually writes. CRD structural validity is enforced by the API server on apply, and these CRDs come from upstream charts rather than being hand-authored here.

Run the "test" step before the "implement" step, see it fail, then make it pass. Commit after each task.

**Things that will bite you if you skip them:**

- `kubectl apply -k` has **no** `--enable-helm` flag. You must pipe `kustomize build --enable-helm` into `kubectl apply`. Every command in this plan already does; don't "simplify" it.
- ArgoCD's CRDs are larger than the 262 144-byte annotation that client-side apply writes. Always `kubectl apply --server-side`.
- The Cilium Helm chart generates Hubble TLS certificates **at template time** by default (`hubble.tls.auto.method: helm`). Every render produces different certificates. Without the `ignoreDifferences` from Task 7, ArgoCD will be permanently `OutOfSync` and will rotate your Hubble certs on every sync.
- Cilium's Application deliberately has **no** `resources-finalizer.argocd.argoproj.io`. Deleting an Application that has one cascade-deletes its resources — in this case, your CNI.

**Cluster access facts:** the kube-apiserver runs on host networking at `192.168.178.16:6443`, so it stays reachable even with Cilium completely broken. `talosctl` does not depend on the CNI either. That is why every recovery path in this plan works.

---

## File structure

| Path | Responsibility |
|---|---|
| `.gitignore` | Exclude render scratch output and OS cruft |
| `README.md` | Architecture, bootstrap, honest caveats |
| `docs/bootstrap.md` | The exact commands to rebuild the ArgoCD layer |
| `docs/runbooks/cilium-recovery.md` | Restore Cilium from a laptop when the CNI is down |
| `docs/runbooks/argocd-recovery.md` | Restore ArgoCD when it has broken itself |
| `docs/decisions/0001-argocd-app-of-apps.md` | ADR: why explicit Applications over ApplicationSets |
| `docs/decisions/0002-kustomize-helm-inflation.md` | ADR: why one rendering language |
| `bootstrap/argocd/kustomization.yaml` | One-line reference to `infrastructure/argocd`; the only thing a human applies |
| `cluster/kustomization.yaml` | Aggregates projects and applications |
| `cluster/root.yaml` | Root Application; applied by hand once |
| `cluster/projects/infrastructure.yaml` | AppProject allowed cluster-scoped resources |
| `cluster/projects/apps.yaml` | AppProject denied cluster-scoped resources |
| `cluster/applications/argocd.yaml` | Application managing ArgoCD itself, wave 0 |
| `cluster/applications/cilium.yaml` | Application managing Cilium, wave -1, no finalizer |
| `infrastructure/argocd/namespace.yaml` | The `argocd` Namespace, so bootstrap creates what it installs into |
| `infrastructure/argocd/values.yaml` | ArgoCD Helm values |
| `infrastructure/argocd/kustomization.yaml` | Inflates the `argo-cd` chart |
| `infrastructure/cilium/values.yaml` | Cilium Helm values, captured verbatim from the live release |
| `infrastructure/cilium/kustomization.yaml` | Inflates the `cilium` chart |
| `.github/workflows/validate.yaml` | prepare → validate → diff → lint |
| `.github/renovate.json5` | Renovate configuration |

`apps/` and `talos/` are created with `.gitkeep` only; they belong to sub-projects #6 and #2.

---

## Task 0: Local prerequisites

**Files:** none — this task installs tools and records versions.

- [ ] **Step 1: Verify cluster access and current state**

```bash
kubectl get nodes -o wide
kubectl get pods -A
helm list -A
```

Expected: two `Ready` nodes (`talos-hzi-iqa`, `talos-zav-s8r`), neither carrying a `NoSchedule` taint. `helm list -A` shows exactly one release: `cilium`, chart `cilium-1.18.0`, in `kube-system`. If `helm list -A` shows an `argocd` release, stop — the spec's premise (ArgoCD was installed from raw manifests) is wrong and Task 6 needs rethinking.

- [ ] **Step 2: Confirm the control-plane taint is gone**

```bash
kubectl get nodes -o custom-columns='NAME:.metadata.name,TAINTS:.spec.taints'
```

Expected: `<none>` for both nodes. If `talos-hzi-iqa` still shows `node-role.kubernetes.io/control-plane:NoSchedule`, remove it:

```bash
kubectl taint node talos-hzi-iqa node-role.kubernetes.io/control-plane:NoSchedule-
```

- [ ] **Step 3: Install the three tools this plan needs**

`kustomize` (standalone — the one bundled in kubectl cannot be driven by `kubectl apply -k` with Helm), `kubeconform`, and `dyff`.

```bash
nix-shell -p kustomize kubeconform dyff --run 'kustomize version; kubeconform -v; dyff version'
```

If you would rather have them permanently, add `kustomize`, `kubeconform` and `dyff` to your nix-darwin `environment.systemPackages` and rebuild. On a non-nix machine: `brew install kustomize kubeconform homeport/tap/dyff`.

Expected: three version strings. Kustomize must be v5.x — `helmCharts` behaves differently on v4.

- [ ] **Step 4: Confirm the GitHub repository is public**

```bash
curl -s https://api.github.com/repos/alex-senger/homelab | grep '"private"'
```

Expected: `"private": false`. ArgoCD clones over anonymous HTTPS; a private repository needs a credential Secret, which breaks this sub-project's zero-secrets property. If it returns `true`, make it public in the GitHub UI (Settings → General → Danger Zone → Change visibility) before Task 6.

- [ ] **Step 5: Resolve the ArgoCD chart version and record it**

```bash
helm repo add argo https://argoproj.github.io/argo-helm >/dev/null
helm repo update argo >/dev/null
helm search repo argo/argo-cd --versions | head -20
```

Pick the newest chart version whose `APP VERSION` column starts with `v3.5.`, matching the v3.5.1 currently running. Write it down — every later task refers to it as `<ARGOCD_CHART_VERSION>` and you substitute the literal number.

Note the `v` prefix: the `APP VERSION` column renders as `v3.5.1`, not `3.5.1`.

```bash
helm search repo argo/argo-cd --versions | awk '$3 ~ /^v3\.5\./ {print $2, $3; exit}'
```

Expected: one line. The first field is the chart version.

**Resolved 2026-08-18: chart `10.4.0`, appVersion `v3.5.1`** — an exact match for the running
installation. Use `10.4.0` wherever this plan says `<ARGOCD_CHART_VERSION>` unless a newer chart
with appVersion `v3.5.x` has since been published.

---

## Task 1: Repository skeleton

**Files:**
- Create: `.gitignore`, `apps/.gitkeep`, `talos/.gitkeep`
- Create: `docs/decisions/0001-argocd-app-of-apps.md`, `docs/decisions/0002-kustomize-helm-inflation.md`

- [ ] **Step 1: Create the directory tree and .gitignore**

```bash
cd ~/workspaces/homelab
mkdir -p bootstrap/argocd cluster/projects cluster/applications \
         infrastructure/cilium infrastructure/argocd \
         apps talos docs/runbooks docs/decisions .github/workflows
touch apps/.gitkeep talos/.gitkeep
```

`.gitignore`:

```gitignore
# Helm chart cache pulled by kustomize --enable-helm
charts/
*.tgz

# OS
.DS_Store
```

Note `charts/` — `kustomize build --enable-helm` downloads charts into a `charts/` subdirectory next to the Kustomization. Those are build artifacts and must never be committed.

There is deliberately no rule for render scratch output. Every render step in this plan writes to the operating system's `/tmp`, which is outside the working tree; a `/tmp/` rule in `.gitignore` would anchor to the repository root and match nothing.

- [ ] **Step 2: Write ADR 0001**

`docs/decisions/0001-argocd-app-of-apps.md`:

```markdown
# 1. Explicit app-of-apps over ApplicationSet generators

Date: 2026-08-18

## Status

Accepted

## Context

ArgoCD offers two ways to declare what runs in a cluster. An ApplicationSet with a Git
directory generator discovers components automatically: add a directory, get an Application.
Alternatively, each component gets an explicit `Application` manifest committed to the repository.

This cluster needs ordered deployment (CNI before anything, secrets management before the
applications that consume secrets), per-component sync policies (Cilium must never self-heal
automatically during adoption), and two AppProjects with different privilege levels.

## Decision

One explicit `Application` per component, all in `cluster/applications/`. A single root
`Application`, applied by hand once, syncs that directory — so ArgoCD manages the set of
Applications the same way it manages everything else.

## Consequences

Adding a component costs roughly fifteen lines of boilerplate.

In exchange, sync ordering is one annotation on one file, and the deployment order of the whole
cluster is readable by listing a single directory. Per-component overrides — `ignoreDifferences`,
sync options, finalizer presence — sit next to the component they affect.

Achieving the same with generators requires a second layer: a `config.yaml` per component read by
a git-files generator. That reproduces this decision's explicitness with more indirection, and
turns "why did this not sync" into debugging a generator rather than reading a file.

Revisit if the component count exceeds roughly thirty, where boilerplate starts to dominate.
```

- [ ] **Step 3: Write ADR 0002**

`docs/decisions/0002-kustomize-helm-inflation.md`:

```markdown
# 2. Kustomize as the only rendering language

Date: 2026-08-18

## Status

Accepted

## Context

Most components in this repository are upstream Helm charts. ArgoCD can consume them three ways:
as a native Helm source with inline values, as a multi-source Application combining a chart with a
values file from Git, or inflated inside a Kustomization via `helmCharts:`.

A stated goal is CI that validates what will actually be applied to the cluster.

## Decision

Every leaf directory is a Kustomization. Helm charts are inflated in place with `helmCharts:`.
This requires `kustomize.buildOptions: --enable-helm` in `argocd-cm`.

## Consequences

Given pinned `kustomize` and `helm` versions, `kustomize build --enable-helm <dir>` on a laptop,
in GitHub Actions, and in the ArgoCD repo-server produce identical output. CI therefore validates
and diffs the real objects rather than approximating them with a separate `helm template`
invocation that may drift.

Chart output stays patchable. Adding an `HTTPRoute` or `ExternalSecret` beside a chart is another
entry in the same Kustomization rather than a second Application source.

The costs: `--enable-helm` must be enabled on the repo-server, and charts are re-downloaded on
each render, mitigated by caching `~/.cache/helm` in CI.

Rollback is `git revert`, not `helm rollback`. This is not a cost of this decision — ArgoCD
templates charts and applies manifests in all three of its Helm modes, so no Helm release object
exists under any of the alternatives considered here.
```

- [ ] **Step 4: Verify nothing is staged that shouldn't be**

```bash
git status --short
```

Expected: only the new files. No `charts/` and no `*.tgz`.

- [ ] **Step 5: Commit**

```bash
git add .gitignore apps talos docs/decisions
git commit -m "chore: scaffold repository structure and record initial ADRs"
```

---

## Task 2: Cilium manifests and the parity gate

**Files:**
- Create: `infrastructure/cilium/values.yaml`
- Create: `infrastructure/cilium/kustomization.yaml`

This task writes Cilium's configuration and proves it matches what is already running. It does **not** put Cilium under ArgoCD — that is Task 7.

- [ ] **Step 1: Capture the live values as the source of truth**

```bash
helm get values cilium -n kube-system -o yaml > /tmp/cilium-live-values.yaml
cat /tmp/cilium-live-values.yaml
```

Expected output — these are Talos-specific and getting them wrong takes down cluster networking:

```yaml
cgroup:
  autoMount:
    enabled: false
  hostRoot: /sys/fs/cgroup
ipam:
  mode: kubernetes
k8sServiceHost: localhost
k8sServicePort: 7445
kubeProxyReplacement: true
securityContext:
  capabilities:
    ciliumAgent:
    - CHOWN
    - KILL
    - NET_ADMIN
    - NET_RAW
    - IPC_LOCK
    - SYS_ADMIN
    - SYS_RESOURCE
    - DAC_OVERRIDE
    - FOWNER
    - SETGID
    - SETUID
    cleanCiliumState:
    - NET_ADMIN
    - SYS_ADMIN
    - SYS_RESOURCE
```

`k8sServiceHost: localhost` with port `7445` is Talos KubePrism — the local API proxy every node runs. `cgroup.autoMount.enabled: false` is required because Talos mounts cgroups itself.

If your output differs from the above, **use your output**, not this plan's. The live cluster is authoritative.

- [ ] **Step 2: Write the values file**

`infrastructure/cilium/values.yaml`:

```yaml
# Captured verbatim from the live Helm release on 2026-08-18 via:
#   helm get values cilium -n kube-system -o yaml
#
# These values are Talos-specific. Do not change them without reading
# docs/runbooks/cilium-recovery.md first — a bad value here takes down
# all pod networking.

# Talos mounts cgroups itself; Cilium must not try.
cgroup:
  autoMount:
    enabled: false
  hostRoot: /sys/fs/cgroup

# Pod CIDRs are allocated by kube-controller-manager, not Cilium.
ipam:
  mode: kubernetes

# Talos KubePrism: the node-local API server proxy. Using it means Cilium
# does not depend on a Service IP that Cilium itself is responsible for.
k8sServiceHost: localhost
k8sServicePort: 7445

# Talos ships no kube-proxy; Cilium provides the equivalent via eBPF.
kubeProxyReplacement: true

# Talos runs a locked-down container runtime, so capabilities are explicit.
securityContext:
  capabilities:
    ciliumAgent:
      - CHOWN
      - KILL
      - NET_ADMIN
      - NET_RAW
      - IPC_LOCK
      - SYS_ADMIN
      - SYS_RESOURCE
      - DAC_OVERRIDE
      - FOWNER
      - SETGID
      - SETUID
    cleanCiliumState:
      - NET_ADMIN
      - SYS_ADMIN
      - SYS_RESOURCE
```

- [ ] **Step 3: Write the Kustomization**

`infrastructure/cilium/kustomization.yaml`:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

helmCharts:
  - name: cilium
    repo: https://helm.cilium.io
    version: 1.18.0
    releaseName: cilium
    namespace: kube-system
    valuesFile: values.yaml
```

`releaseName: cilium` and `namespace: kube-system` must match the live release exactly, or every rendered resource name and namespace will differ from what is running.

**There is deliberately no `includeCRDs: true`.** Verified 2026-08-18: the Cilium chart ships no
`crds/` directory and no `CustomResourceDefinition` templates at all, while the cluster has 10
registered `cilium.io` CRDs. Cilium's agent and operator create them programmatically at startup.
`includeCRDs` would therefore be a no-op that falsely implies this Kustomization manages them.

This is a real limit on the repository's "everything is declared in Git" claim, and the README
states it: Cilium's CRDs are the one part of the CNI that is not declarative.

- [ ] **Step 4: Run the test — render and schema-validate**

```bash
cd ~/workspaces/homelab
kustomize build --enable-helm infrastructure/cilium > /tmp/cilium-rendered.yaml
kubeconform -strict -summary -skip CustomResourceDefinition \
  -schema-location default \
  -schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json' \
  /tmp/cilium-rendered.yaml
```

Expected: a summary line reporting resources parsed, `0 errors`, `0 skipped`. If `kustomize build` fails with `must specify --enable-helm`, you omitted the flag. If it fails pulling the chart, check network and that `version: 1.18.0` exists.

- [ ] **Step 5: Run the gate — parity against the live manifest**

This is the step that makes Task 7 safe.

```bash
helm get manifest cilium -n kube-system > /tmp/cilium-live.yaml
dyff between --omit-header /tmp/cilium-live.yaml /tmp/cilium-rendered.yaml \
  | sed -E 's/(LS0tLS1CRUdJT|[A-Za-z0-9+\/]{60,}=*)/<REDACTED-KEY-MATERIAL>/g'
```

**Never paste the unredacted diff into a chat, a report, an issue, or a commit message.** The
`data` field of a TLS Secret *is* the private key; diffing two of them prints both keys in full.
The `sed` filter above collapses long base64 runs so the structural diff stays readable while the
key material does not leave the terminal. Run the unfiltered command only when you need to inspect
a specific non-Secret resource, and do not copy its output anywhere.

Expected: no differences, or differences **only** in Hubble TLS certificate Secrets and the `cilium-ca` Secret.

Those certificate differences are expected and are not a mistake on your part: the Cilium chart's default `hubble.tls.auto.method` is `helm`, which generates certificates during templating, so every render produces different key material. Task 7 handles this with `ignoreDifferences`.

**Any other difference is a stop condition.** Do not proceed to Task 7. Reconcile `values.yaml` against `helm get values` output until only certificate Secrets differ. A difference in the DaemonSet, the ConfigMap, or any RBAC object means the rendered configuration is not what is running, and syncing it would change live networking behaviour.

- [ ] **Step 6: Record which Secrets differ — Task 7 needs the exact names**

```bash
dyff between --omit-header /tmp/cilium-live.yaml /tmp/cilium-rendered.yaml \
  | grep -iE '^v1/Secret|secret' || echo "no secret differences"
grep -E '^  name: (hubble|cilium-ca)' /tmp/cilium-rendered.yaml | sort -u
```

Write down the exact Secret names. Expect `cilium-ca` and `hubble-server-certs` in `kube-system`. `hubble-relay-client-certs` appears only if Hubble Relay is enabled, which these values do not enable.

- [ ] **Step 7: Commit**

```bash
git add infrastructure/cilium
git commit -m "feat(cilium): declare live Helm values as GitOps source of truth

Values captured verbatim from the running release. Rendered output
verified byte-for-byte against 'helm get manifest cilium' apart from
Hubble TLS secrets, which the chart regenerates on every template."
```

---

## Task 3: ArgoCD manifests

**Files:**
- Create: `infrastructure/argocd/namespace.yaml`
- Create: `infrastructure/argocd/values.yaml`
- Create: `infrastructure/argocd/kustomization.yaml`

Substitute the chart version you recorded in Task 0 Step 5 wherever this task says `<ARGOCD_CHART_VERSION>`.

- [ ] **Step 1: Write the Namespace**

Task 6 deletes the `argocd` namespace, so the repository must recreate it. This is why bootstrap does not rely on `CreateNamespace=true`.

`infrastructure/argocd/namespace.yaml`:

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: argocd
```

- [ ] **Step 2: Write the values file**

`infrastructure/argocd/values.yaml`:

```yaml
# ArgoCD configuration for the roastery cluster.
#
# Resource requests are sized for 2 vCPU / 4 GB nodes. Revisit after the
# host RAM upgrade (see spec section 3).

configs:
  cm:
    # Required for helmCharts: inflation in every Kustomization in this repo.
    # Without it the repo-server renders nothing and every Application fails.
    kustomize.buildOptions: --enable-helm

    # Track managed resources with an annotation instead of the default
    # app.kubernetes.io/instance label. The default label collides with
    # labels charts already set on their own resources — Cilium in particular.
    application.resourceTrackingMethod: annotation

  params:
    # TLS terminates at the Gateway from sub-project #3 onward. Until then
    # access is via kubectl port-forward, so serving plaintext internally
    # avoids the self-signed-certificate warning loop.
    server.insecure: true

# Disabled to reclaim roughly 100 MB on a 4 GB node.
# Dex returns in sub-project #6, when Authelia becomes the OIDC provider.
dex:
  enabled: false

notifications:
  enabled: false

controller:
  resources:
    requests:
      cpu: 100m
      memory: 256Mi
    limits:
      memory: 768Mi

repoServer:
  resources:
    requests:
      cpu: 50m
      memory: 128Mi
    limits:
      memory: 512Mi

server:
  resources:
    requests:
      cpu: 50m
      memory: 128Mi
    limits:
      memory: 256Mi

redis:
  resources:
    requests:
      cpu: 25m
      memory: 64Mi
    limits:
      memory: 192Mi

applicationSet:
  resources:
    requests:
      cpu: 25m
      memory: 64Mi
    limits:
      memory: 192Mi
```

No CPU limits are set deliberately. On a 4-thread host, CPU limits cause throttling that looks like a hang; memory limits are the ones that protect the node.

- [ ] **Step 3: Write the Kustomization**

`infrastructure/argocd/kustomization.yaml`:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

namespace: argocd

resources:
  - namespace.yaml

helmCharts:
  - name: argo-cd
    repo: https://argoproj.github.io/argo-helm
    version: <ARGOCD_CHART_VERSION>
    releaseName: argocd
    namespace: argocd
    valuesFile: values.yaml
```

**No `includeCRDs`, for a different reason than Cilium's.** Verified 2026-08-18: the argo-cd chart
ships its three CRDs as ordinary templates under `templates/crds/`, gated by the chart value
`crds.install` (default `true`) — not via Helm's special `crds/` directory, which is the only thing
`--include-crds` controls. Rendering with and without the flag produces byte-identical output.
Including it would falsely imply this Kustomization decides whether CRDs are installed; the chart's
own default already decided.

- [ ] **Step 4: Run the test — render and schema-validate**

```bash
kustomize build --enable-helm infrastructure/argocd > /tmp/argocd-rendered.yaml
kubeconform -strict -summary -skip CustomResourceDefinition \
  -schema-location default \
  -schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json' \
  /tmp/argocd-rendered.yaml
```

Expected: `0 errors`.

- [ ] **Step 5: Verify the three things that must be in the output**

```bash
# CRDs present exactly once each
grep -c '^kind: CustomResourceDefinition' /tmp/argocd-rendered.yaml
grep -E '^  name: (applications|applicationsets|appprojects)\.argoproj\.io' /tmp/argocd-rendered.yaml | sort | uniq -c

# The two ConfigMap settings this whole repository depends on
grep -A2 'kustomize.buildOptions' /tmp/argocd-rendered.yaml
grep 'resourceTrackingMethod' /tmp/argocd-rendered.yaml

# Dex and notifications really are gone
grep -c 'argocd-dex-server\|argocd-notifications' /tmp/argocd-rendered.yaml
```

Expected: exactly 3 CRDs, each name appearing exactly once; `kustomize.buildOptions: --enable-helm` and `application.resourceTrackingMethod: annotation` both present; exactly one `Namespace`; the image tag `quay.io/argoproj/argocd:v3.5.1`, matching what is running.

The dex/notifications grep returns `3`, not `0` — and that is correct. All three hits are `argocd-dex-server-tls` Secret and volume *references* inside the argocd-server Deployment, which the chart emits regardless. Confirm the components are genuinely absent by checking for workloads rather than string matches:

```bash
grep -E '^kind: (Deployment|StatefulSet)$' -A3 /tmp/argocd-rendered.yaml | grep 'name:' | sort
```

Expected: `argocd-application-controller`, `argocd-applicationset-controller`, `argocd-redis`, `argocd-repo-server`, `argocd-server` — and no dex or notifications workload.

Total memory limits across all five workloads come to roughly 1.9 GB, which fits a 4 GB node alongside Cilium and system overhead.

- [ ] **Step 6: Commit**

```bash
git add infrastructure/argocd
git commit -m "feat(argocd): declare ArgoCD via Helm chart with GitOps prerequisites

Enables kustomize --enable-helm on the repo-server and switches resource
tracking to annotations so managed resources keep their chart-set labels.
Dex and notifications disabled to fit 4 GB nodes."
```

---

## Task 4: The bootstrap indirection

**Files:**
- Create: `bootstrap/argocd/kustomization.yaml`

- [ ] **Step 1: Write the one-line reference**

`bootstrap/argocd/kustomization.yaml`:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

# Deliberately nothing but a reference.
#
# There is exactly one definition of ArgoCD in this repository, at
# infrastructure/argocd. The usual failure of a self-managing ArgoCD is a
# bootstrap manifest that drifts from the GitOps-managed one, leaving the
# Application permanently OutOfSync. Keeping this file empty of content makes
# that impossible.
#
# Apply with (kubectl apply -k cannot inflate Helm charts):
#   kustomize build --enable-helm bootstrap/argocd | kubectl apply --server-side -f -
resources:
  - ../../infrastructure/argocd
```

- [ ] **Step 2: Run the test — prove it renders identically to the real thing**

```bash
kustomize build --enable-helm bootstrap/argocd > /tmp/bootstrap-rendered.yaml
dyff between --omit-header /tmp/argocd-rendered.yaml /tmp/bootstrap-rendered.yaml
```

Expected: **zero** differences. Not "only certificates" — zero. If anything differs, the indirection is broken and the anti-drift property this file exists for does not hold.

- [ ] **Step 3: Commit**

```bash
git add bootstrap/argocd
git commit -m "feat(bootstrap): add single-source ArgoCD bootstrap entrypoint"
```

---

## Task 5: The ArgoCD control plane

**Files:**
- Create: `cluster/projects/infrastructure.yaml`, `cluster/projects/apps.yaml`
- Create: `cluster/applications/argocd.yaml`
- Create: `cluster/kustomization.yaml`
- Create: `cluster/root.yaml`

Cilium's Application is deliberately **not** created here. It arrives in Task 7, after ArgoCD is proven self-managing.

- [ ] **Step 1: Write the infrastructure AppProject**

`cluster/projects/infrastructure.yaml`:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: AppProject
metadata:
  name: infrastructure
  namespace: argocd
spec:
  description: >-
    Platform components. May create cluster-scoped resources: CNI, CRDs,
    storage drivers, admission controllers.

  sourceRepos:
    - https://github.com/alex-senger/homelab.git

  destinations:
    - server: https://kubernetes.default.svc
      namespace: '*'

  clusterResourceWhitelist:
    - group: '*'
      kind: '*'

  namespaceResourceWhitelist:
    - group: '*'
      kind: '*'
```

Only the Git repository is listed in `sourceRepos`. `sourceRepos` validates `spec.source.repoURL`; the Helm repositories are fetched by the repo-server during `kustomize build` and are not Application sources.

- [ ] **Step 2: Write the apps AppProject**

`cluster/projects/apps.yaml`:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: AppProject
metadata:
  name: apps
  namespace: argocd
spec:
  description: >-
    User-facing workloads. Denied cluster-scoped resources so a third-party
    chart cannot grant itself cluster-admin or install CRDs.

  sourceRepos:
    - https://github.com/alex-senger/homelab.git

  # Namespaces planned in sub-project #6. Extend as applications are added;
  # an application targeting an unlisted namespace is refused.
  destinations:
    - server: https://kubernetes.default.svc
      namespace: website
    - server: https://kubernetes.default.svc
      namespace: authelia
    - server: https://kubernetes.default.svc
      namespace: nextcloud

  # Empty: no cluster-scoped resource of any kind is permitted.
  clusterResourceWhitelist: []

  namespaceResourceWhitelist:
    - group: '*'
      kind: '*'
```

- [ ] **Step 3: Write ArgoCD's own Application**

`cluster/applications/argocd.yaml`:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: argocd
  namespace: argocd
  annotations:
    argocd.argoproj.io/sync-wave: "0"
  finalizers:
    - resources-finalizer.argocd.argoproj.io
spec:
  project: infrastructure

  source:
    repoURL: https://github.com/alex-senger/homelab.git
    targetRevision: main
    path: infrastructure/argocd

  destination:
    server: https://kubernetes.default.svc
    namespace: argocd

  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      # ArgoCD's CRDs exceed the 262144-byte annotation that client-side
      # apply writes. Without this, self-management fails on the CRDs.
      - ServerSideApply=true
```

- [ ] **Step 4: Write the cluster aggregator**

`cluster/kustomization.yaml`:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

namespace: argocd

resources:
  - projects/infrastructure.yaml
  - projects/apps.yaml
  - applications/argocd.yaml
```

`root.yaml` is deliberately absent from this list. The root Application is applied by hand and must not manage itself.

- [ ] **Step 5: Write the root Application**

`cluster/root.yaml`:

```yaml
# Applied by hand exactly once, after bootstrap:
#   kubectl apply -f cluster/root.yaml
#
# Uses project 'default' because the infrastructure and apps projects are
# created by this Application's own first sync — they do not exist yet when
# it is applied.
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: root
  namespace: argocd
spec:
  project: default

  source:
    repoURL: https://github.com/alex-senger/homelab.git
    targetRevision: main
    path: cluster

  destination:
    server: https://kubernetes.default.svc
    namespace: argocd

  syncPolicy:
    automated:
      # Safe here: this Application manages only Application and AppProject
      # resources. Pruning an Application does not prune what it manages
      # unless that Application carries a finalizer.
      prune: true
      selfHeal: true
```

- [ ] **Step 6: Run the test — render and schema-validate**

```bash
kustomize build cluster > /tmp/cluster-rendered.yaml
kubeconform -strict -summary -skip CustomResourceDefinition \
  -schema-location default \
  -schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json' \
  /tmp/cluster-rendered.yaml cluster/root.yaml
```

No `--enable-helm` is needed — `cluster/` contains no charts.

Expected: `0 errors` across three resources from the Kustomization plus the root Application. If kubeconform reports the `argoproj.io` kinds as skipped rather than validated, the CRDs-catalog schema URL is wrong or unreachable — fix it now, because CI depends on the same URL.

- [ ] **Step 7: Commit**

```bash
git add cluster
git commit -m "feat(cluster): add app-of-apps control plane

Two AppProjects with different privilege levels: infrastructure may create
cluster-scoped resources, apps may not. ArgoCD manages itself at sync wave 0
with server-side apply."
```

---

## Task 6: Reinstall ArgoCD from the repository

**Files:** none — this task changes cluster state.

This is destructive and irreversible in the sense that the running ArgoCD is deleted. It is safe because the only ArgoCD state in the cluster is the auto-created `default` AppProject. Read Task 6 Step 1 before doing anything.

- [ ] **Step 1: Confirm there is genuinely nothing to lose**

```bash
kubectl get applications,applicationsets -n argocd
kubectl get appprojects -n argocd
```

Expected: no Applications, no ApplicationSets, and exactly one AppProject named `default`. If any Application exists, **stop** — something is being managed that this task would delete. Export it and reassess.

- [ ] **Step 2: Understand why this is a reinstall and not an adoption**

```bash
kubectl get deploy argocd-server -n argocd -o jsonpath='{.spec.selector.matchLabels}'; echo
grep -A5 'kind: Deployment' /tmp/argocd-rendered.yaml | grep -A3 'argocd-server' || true
kubectl get deploy argocd-server -n argocd -o jsonpath='{.metadata.labels}'; echo
```

The running Deployment came from raw `install.yaml` and selects on `app.kubernetes.io/name` alone. The chart's Deployment also selects on `app.kubernetes.io/instance`. `spec.selector` is immutable, so syncing the chart over the existing Deployment fails with a field-immutable error. There is no adoption path; deletion is the only option.

- [ ] **Step 3: Delete the namespace**

```bash
kubectl delete namespace argocd --wait=true
kubectl get namespace argocd
```

Expected: the second command reports `NotFound`. The Argo CRDs are cluster-scoped and survive; verify:

```bash
kubectl get crd | grep argoproj.io
```

Expected: `applications`, `applicationsets`, `appprojects`.

- [ ] **Step 4: Bootstrap from the repository**

```bash
cd ~/workspaces/homelab
kustomize build --enable-helm bootstrap/argocd | kubectl apply --server-side -f -
```

If this reports conflicts on the CRDs (another field manager owns them — the old raw install), add `--force-conflicts`:

```bash
kustomize build --enable-helm bootstrap/argocd | kubectl apply --server-side --force-conflicts -f -
```

- [ ] **Step 5: Wait for ArgoCD to be ready**

```bash
kubectl -n argocd rollout status deploy/argocd-server --timeout=300s
kubectl -n argocd rollout status deploy/argocd-repo-server --timeout=300s
kubectl -n argocd get pods
```

Expected: all pods `Running`. There must be **no** `argocd-dex-server` and no `argocd-notifications-controller` — if they exist, `values.yaml` was not applied and you rendered the wrong path.

- [ ] **Step 6: Verify the two critical ConfigMap settings landed**

```bash
kubectl -n argocd get cm argocd-cm -o jsonpath='{.data.kustomize\.buildOptions}'; echo
kubectl -n argocd get cm argocd-cm -o jsonpath='{.data.application\.resourceTrackingMethod}'; echo
```

Expected: `--enable-helm` and `annotation`. If either is empty, every subsequent sync will fail; fix `infrastructure/argocd/values.yaml` and re-run Step 4 before continuing.

- [ ] **Step 7: Hand over to GitOps**

```bash
kubectl apply -f cluster/root.yaml
```

- [ ] **Step 8: Verify self-management**

```bash
kubectl -n argocd get applications
```

Expected, within a minute or two: `root` and `argocd`, both `Synced` and `Healthy`.

If `argocd` is `OutOfSync`, inspect the diff:

```bash
kubectl -n argocd get application argocd -o jsonpath='{.status.conditions}' | python3 -m json.tool
```

A perpetual `OutOfSync` here means `bootstrap/` and `infrastructure/argocd` rendered differently, which Task 4 Step 2 should have caught.

- [ ] **Step 9: Run the drift test — the only check that proves GitOps works**

```bash
kubectl -n argocd scale deploy/argocd-repo-server --replicas=2
kubectl -n argocd get deploy argocd-repo-server -w
```

Expected: replicas go to 2, then self-heal returns them to 1 within about 30 seconds. Press Ctrl-C once it reverts.

If it does not revert, `selfHeal: true` is not in effect. Check `kubectl -n argocd get application argocd -o jsonpath='{.spec.syncPolicy}'`.

This is worth recording as a terminal GIF for the README — it is the single most convincing artefact in the whole repository.

- [ ] **Step 10: Get the admin password and confirm the UI**

```bash
kubectl -n argocd get secret argocd-initial-admin-secret \
  -o jsonpath='{.data.password}' | base64 -d; echo
kubectl -n argocd port-forward svc/argocd-server 8080:80
```

Open `http://localhost:8080`, log in as `admin`. Expected: two Applications, both green. Ctrl-C the port-forward when done.

- [ ] **Step 11: Commit the verification record**

Nothing changed in the repository, so record the outcome in the runbook instead — written in full in Task 11. Skip committing here and proceed to Task 7.

---

## Task 7: Bring Cilium under ArgoCD, manual sync only

**Files:**
- Create: `cluster/applications/cilium.yaml`
- Modify: `cluster/kustomization.yaml`

- [ ] **Step 1: Re-run the parity gate against current live state**

State may have changed since Task 2. Re-verify before letting ArgoCD near the CNI.

```bash
cd ~/workspaces/homelab
kustomize build --enable-helm infrastructure/cilium > /tmp/cilium-rendered.yaml
helm get manifest cilium -n kube-system > /tmp/cilium-live.yaml
dyff between --omit-header /tmp/cilium-live.yaml /tmp/cilium-rendered.yaml
```

Expected: differences only in `cilium-ca` and `hubble-server-certs`. Anything else is a stop condition.

- [ ] **Step 2: Write Cilium's Application**

`cluster/applications/cilium.yaml`:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: cilium
  namespace: argocd
  annotations:
    # Wave -1: the CNI must exist before anything that needs pod networking.
    argocd.argoproj.io/sync-wave: "-1"
  # NO finalizer, deliberately.
  #
  # resources-finalizer.argocd.argoproj.io makes deleting this Application
  # cascade-delete everything it manages. For this Application that means
  # deleting the CNI and taking down all pod networking cluster-wide.
  # Recovery would require the runbook in docs/runbooks/cilium-recovery.md.
spec:
  project: infrastructure

  source:
    repoURL: https://github.com/alex-senger/homelab.git
    targetRevision: main
    path: infrastructure/cilium

  destination:
    server: https://kubernetes.default.svc
    namespace: kube-system

  # Manual sync during adoption. Automated sync is enabled in Task 8, after
  # a manual sync has been verified clean.
  syncPolicy:
    syncOptions:
      - ServerSideApply=true
      - RespectIgnoreDifferences=true

  # The Cilium chart's default hubble.tls.auto.method is "helm", which
  # generates certificates at template time. Every render therefore produces
  # different key material. Without these rules ArgoCD reports permanent
  # drift and rotates Hubble's certificates on every single sync.
  #
  # Sub-project #3 replaces this by moving Hubble TLS to cert-manager.
  ignoreDifferences:
    - group: ""
      kind: Secret
      name: cilium-ca
      namespace: kube-system
      jsonPointers:
        - /data
    - group: ""
      kind: Secret
      name: hubble-server-certs
      namespace: kube-system
      jsonPointers:
        - /data
```

If Task 2 Step 6 found Secret names beyond these two, add a matching block for each.

- [ ] **Step 3: Register it with the aggregator**

`cluster/kustomization.yaml` — add one line so the resources list reads:

```yaml
resources:
  - projects/infrastructure.yaml
  - projects/apps.yaml
  - applications/argocd.yaml
  - applications/cilium.yaml
```

- [ ] **Step 4: Run the test — render and validate**

```bash
kustomize build cluster > /tmp/cluster-rendered.yaml
kubeconform -strict -summary -skip CustomResourceDefinition \
  -schema-location default \
  -schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json' \
  /tmp/cluster-rendered.yaml
grep -c 'resources-finalizer' /tmp/cluster-rendered.yaml
```

Expected: `0 errors`, and the finalizer count is `1` — ArgoCD's Application has one, Cilium's does not.

- [ ] **Step 5: Commit and push so ArgoCD can see it**

```bash
git add cluster/applications/cilium.yaml cluster/kustomization.yaml
git commit -m "feat(cilium): add Application with manual sync for adoption

No finalizer: deleting this Application must never cascade to the CNI.
ignoreDifferences on Hubble TLS secrets because the chart regenerates
certificates at template time."
git push
```

- [ ] **Step 6: Watch ArgoCD discover it**

```bash
kubectl -n argocd get applications -w
```

Expected: `cilium` appears within about three minutes, showing `OutOfSync` and `Healthy`. `OutOfSync` is correct — sync is manual and nothing has synced yet. Ctrl-C.

- [ ] **Step 7: Inspect the diff ArgoCD sees before syncing**

```bash
kubectl -n argocd port-forward svc/argocd-server 8080:80 &
sleep 3
```

Open `http://localhost:8080`, click the `cilium` Application, then **App Diff**.

Expected: no diff, or diff confined to the two Secrets. If ArgoCD wants to change the DaemonSet, the ConfigMap, or any RBAC object, **do not sync**. Kill the port-forward, return to Step 1, and reconcile values.

- [ ] **Step 8: Sync manually, with the recovery command already in your clipboard**

Have this ready in a second terminal **before** you sync — add the repo now, so recovery is a
single paste under pressure:

```bash
helm repo add cilium https://helm.cilium.io && helm repo update cilium
# recovery command, do not run unless networking breaks:
#   helm upgrade cilium cilium/cilium --version 1.18.0 \
#     -n kube-system -f infrastructure/cilium/values.yaml
```

Then sync:

```bash
kubectl -n argocd patch application cilium --type merge \
  -p '{"operation":{"sync":{"revision":"main"}}}'
```

- [ ] **Step 9: Verify the cluster is still healthy**

```bash
kubectl -n argocd get application cilium
cilium status --wait
kubectl get pods -A | grep -vE 'Running|Completed' || echo "all pods healthy"
kubectl run conn-test --rm -it --restart=Never --image=busybox:1.36 -- \
  sh -c 'nslookup kubernetes.default.svc.cluster.local && echo DNS_OK'
```

Expected: Application `Synced`/`Healthy`; `cilium status` reporting all daemonsets ready and no errors; no unhealthy pods; `DNS_OK` printed.

If pod networking is broken, run the `helm upgrade` from Step 8 immediately. The apiserver is on host networking so `kubectl` keeps working throughout.

- [ ] **Step 10: Kill the port-forward**

```bash
kill %1 2>/dev/null || true
```

---

## Task 8: Enable automated sync and remove the old Helm release

**Files:**
- Modify: `cluster/applications/cilium.yaml`

Deliberately a separate commit from Task 7, so that `git revert` returns Cilium to manual sync in one step.

- [ ] **Step 1: Confirm Cilium has been stably synced**

```bash
kubectl -n argocd get application cilium \
  -o jsonpath='{.status.sync.status}{"\n"}{.status.health.status}{"\n"}'
```

Expected: `Synced` and `Healthy`. If it drifted back to `OutOfSync` since Task 7, the `ignoreDifferences` rules do not cover everything the chart regenerates. Find what:

```bash
kubectl -n argocd get application cilium -o json \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(json.dumps(d["status"].get("resources",[]), indent=2))' \
  | grep -B3 OutOfSync
```

Add `ignoreDifferences` entries for whatever appears, and do not proceed until it holds `Synced` for several minutes.

- [ ] **Step 2: Add automated sync**

In `cluster/applications/cilium.yaml`, replace the `syncPolicy` block with:

```yaml
  syncPolicy:
    automated:
      # prune stays false: pruning a CNI resource on a transient render
      # failure would take down cluster networking.
      prune: false
      selfHeal: true
    syncOptions:
      - ServerSideApply=true
      - RespectIgnoreDifferences=true
```

`prune: false` is permanent for this component, not a temporary caution. Removing a Cilium resource is a deliberate act that should be done by a human reading the diff.

- [ ] **Step 3: Run the test, commit, push**

```bash
kustomize build cluster | kubeconform -strict -summary -skip CustomResourceDefinition \
  -schema-location default \
  -schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'
git add cluster/applications/cilium.yaml
git commit -m "feat(cilium): enable automated sync with self-heal

Pruning stays disabled permanently: removing a CNI resource must be a
deliberate human action, not a consequence of a render failure."
git push
```

Expected: `0 errors`.

- [ ] **Step 4: Verify self-heal works on Cilium too**

Drift the DaemonSet's **metadata label**, not its pod template. A label on the DaemonSet object
does not touch the pod spec, so no Cilium pods restart and pod networking is never disrupted. Do
**not** use `kubectl set env` here — that mutates the pod template and triggers a rolling restart
of the CNI on a live cluster.

```bash
kubectl -n kube-system label daemonset cilium drift-test=true --overwrite
sleep 60
kubectl -n kube-system get daemonset cilium -o jsonpath='{.metadata.labels}' | grep -q drift-test \
  && echo "STILL PRESENT — self-heal did not fire" \
  || echo "reverted"
```

Expected: `reverted`. If it says `STILL PRESENT`, self-heal is not active — check
`kubectl -n argocd get application cilium -o jsonpath='{.spec.syncPolicy}'` and remove the drift
by hand:

```bash
kubectl -n kube-system label daemonset cilium drift-test-
```

- [ ] **Step 5: Delete the stale Helm release metadata**

Two sources of truth is the problem this repository exists to eliminate.

```bash
kubectl get secret -n kube-system -l 'owner=helm,name=cilium'
kubectl delete secret -n kube-system -l 'owner=helm,name=cilium'
helm list -A
```

Expected: `helm list -A` returns an empty list. This is a named item in the spec's definition of done.

- [ ] **Step 6: Confirm the cluster is unaffected**

```bash
cilium status --wait
kubectl -n argocd get applications
```

Expected: Cilium healthy; `root`, `argocd`, `cilium` all `Synced`/`Healthy`. Deleting the release metadata touches no live resource — it only removes Helm's bookkeeping.

---

## Task 9: CI validation

**Files:**
- Create: `.github/workflows/validate.yaml`

- [ ] **Step 1: Write the workflow**

`.github/workflows/validate.yaml`:

```yaml
name: validate

on:
  pull_request:
  push:
    branches: [main]

permissions:
  contents: read

env:
  # renovate: datasource=github-releases depName=kubernetes-sigs/kustomize
  KUSTOMIZE_VERSION: 5.8.1
  # renovate: datasource=github-releases depName=yannh/kubeconform
  KUBECONFORM_VERSION: 0.7.0
  # renovate: datasource=github-releases depName=homeport/dyff
  DYFF_VERSION: 1.10.1
  CRD_SCHEMAS: https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json

jobs:
  prepare:
    runs-on: ubuntu-latest
    outputs:
      dirs: ${{ steps.discover.outputs.dirs }}
    steps:
      - uses: actions/checkout@v4

      - name: Discover every Kustomization
        id: discover
        run: |
          dirs=$(find . -name kustomization.yaml -not -path './.git/*' -printf '%h\n' \
                 | sed 's|^\./||' | sort -u | jq -R . | jq -sc .)
          echo "dirs=$dirs" >> "$GITHUB_OUTPUT"
          echo "Discovered: $dirs"

  validate:
    needs: prepare
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        dir: ${{ fromJson(needs.prepare.outputs.dirs) }}
    steps:
      - uses: actions/checkout@v4

      - uses: azure/setup-helm@v4

      - name: Cache Helm chart downloads
        uses: actions/cache@v4
        with:
          path: ~/.cache/helm
          key: helm-${{ hashFiles('**/kustomization.yaml') }}
          restore-keys: helm-

      - name: Install kustomize and kubeconform
        run: |
          curl -sSfL "https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize%2Fv${KUSTOMIZE_VERSION}/kustomize_v${KUSTOMIZE_VERSION}_linux_amd64.tar.gz" \
            | sudo tar -xz -C /usr/local/bin kustomize
          curl -sSfL "https://github.com/yannh/kubeconform/releases/download/v${KUBECONFORM_VERSION}/kubeconform-linux-amd64.tar.gz" \
            | sudo tar -xz -C /usr/local/bin kubeconform

      - name: Render and schema-validate ${{ matrix.dir }}
        run: |
          kustomize build --enable-helm "${{ matrix.dir }}" > /tmp/rendered.yaml
          kubeconform -strict -summary -skip CustomResourceDefinition \
            -schema-location default \
            -schema-location "$CRD_SCHEMAS" \
            /tmp/rendered.yaml

  # cluster/root.yaml belongs to no Kustomization by design — it is applied by
  # hand and must not manage itself, so it is excluded from cluster/kustomization.yaml.
  # That also means the matrix above never sees it. Validate it explicitly, or it
  # is the one manifest in the repository CI does not check.
  standalone:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Install kubeconform
        run: |
          curl -sSfL "https://github.com/yannh/kubeconform/releases/download/v${KUBECONFORM_VERSION}/kubeconform-linux-amd64.tar.gz" \
            | sudo tar -xz -C /usr/local/bin kubeconform

      - name: Assert the standalone list is still complete
        run: |
          # Every .yaml under cluster/ except root.yaml must be referenced by
          # cluster/kustomization.yaml. If someone adds another unreferenced
          # manifest, fail loudly rather than silently skipping it.
          for f in $(find cluster -name '*.yaml' -not -name 'kustomization.yaml' -not -name 'root.yaml'); do
            base=$(basename "$f")
            grep -q "$base" cluster/kustomization.yaml || {
              echo "::error::$f is not referenced by cluster/kustomization.yaml and is not in the standalone list"
              exit 1
            }
          done

      - name: Validate standalone manifests
        run: |
          kubeconform -strict -summary -skip CustomResourceDefinition \
            -schema-location default \
            -schema-location "$CRD_SCHEMAS" \
            cluster/root.yaml

  diff:
    needs: prepare
    if: github.event_name == 'pull_request'
    runs-on: ubuntu-latest
    permissions:
      contents: read
      pull-requests: write
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - uses: azure/setup-helm@v4

      - name: Install kustomize and dyff
        run: |
          curl -sSfL "https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize%2Fv${KUSTOMIZE_VERSION}/kustomize_v${KUSTOMIZE_VERSION}_linux_amd64.tar.gz" \
            | sudo tar -xz -C /usr/local/bin kustomize
          curl -sSfL "https://github.com/homeport/dyff/releases/download/v${DYFF_VERSION}/dyff_${DYFF_VERSION}_linux_amd64.tar.gz" \
            | sudo tar -xz -C /usr/local/bin dyff

      - name: Render base and head, then diff
        id: render
        env:
          BASE_SHA: ${{ github.event.pull_request.base.sha }}
        run: |
          git worktree add /tmp/base "$BASE_SHA"

          {
            echo "## Rendered manifest changes"
            echo
            for dir in $(echo '${{ needs.prepare.outputs.dirs }}' | jq -r '.[]'); do
              head_out=/tmp/head.yaml
              base_out=/tmp/base.yaml
              kustomize build --enable-helm "$dir" > "$head_out" 2>/dev/null || echo "" > "$head_out"

              if [ -f "/tmp/base/$dir/kustomization.yaml" ]; then
                (cd /tmp/base && kustomize build --enable-helm "$dir") > "$base_out" 2>/dev/null || echo "" > "$base_out"
              else
                echo "" > "$base_out"
                echo "### \`$dir\` — new component"
                echo
              fi

              if out=$(dyff between --omit-header --set-exit-code "$base_out" "$head_out" 2>&1); then
                continue    # identical, say nothing
              fi
              echo "### \`$dir\`"
              echo
              echo '```diff'
              # MANDATORY redaction. Rendering Cilium produces TLS Secrets whose
              # data fields are private keys, and the chart regenerates them on
              # every template — so they appear in almost every diff. Without this
              # filter the job publishes live private keys into a PR comment on a
              # public repository.
              echo "$out" | sed -E 's/[A-Za-z0-9+\/]{60,}=*/<REDACTED-KEY-MATERIAL>/g'
              echo '```'
              echo
            done
          } > /tmp/comment.md

          if [ "$(wc -l < /tmp/comment.md)" -le 3 ]; then
            echo "## Rendered manifest changes" > /tmp/comment.md
            echo >> /tmp/comment.md
            echo "No changes to rendered Kubernetes objects." >> /tmp/comment.md
          fi

          cat /tmp/comment.md

      - name: Post the diff as a sticky comment
        uses: marocchino/sticky-pull-request-comment@v2
        with:
          header: rendered-diff
          path: /tmp/comment.md

  lint:
    needs: prepare
    runs-on: ubuntu-latest
    # Advisory only. Upstream charts trip these checks, and a permanently
    # failing required check is a check nobody reads.
    continue-on-error: true
    steps:
      - uses: actions/checkout@v4

      - uses: azure/setup-helm@v4

      - name: Install kustomize
        run: |
          curl -sSfL "https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize%2Fv${KUSTOMIZE_VERSION}/kustomize_v${KUSTOMIZE_VERSION}_linux_amd64.tar.gz" \
            | sudo tar -xz -C /usr/local/bin kustomize

      - name: Render everything into one directory
        run: |
          mkdir -p /tmp/lint
          : > /tmp/lint/all.yaml
          for dir in $(echo '${{ needs.prepare.outputs.dirs }}' | jq -r '.[]'); do
            echo "---" >> /tmp/lint/all.yaml
            kustomize build --enable-helm "$dir" >> /tmp/lint/all.yaml
          done

      # The action's `directory` input expects a directory, not a file.
      - uses: stackrox/kube-linter-action@v1
        with:
          directory: /tmp/lint
```

- [ ] **Step 2: Run the test locally before pushing — reproduce the matrix by hand**

```bash
cd ~/workspaces/homelab
for dir in $(find . -name kustomization.yaml -not -path './.git/*' | xargs -n1 dirname | sed 's|^\./||' | sort -u); do
  echo "=== $dir"
  kustomize build --enable-helm "$dir" \
    | kubeconform -strict -summary -skip CustomResourceDefinition \
        -schema-location default \
        -schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'
done
```

Expected: four directories, with these exact sizes — `bootstrap/argocd` 45 resources (42 valid, 3 skipped), `cluster` 3 resources, `infrastructure/argocd` 45 resources (42 valid, 3 skipped), `infrastructure/cilium` 23 resources (23 valid). `bootstrap/argocd` and `infrastructure/argocd` matching exactly is the anti-drift property from Task 4 holding.

Note `find` here uses `xargs dirname` because macOS `find` lacks GNU's `-printf`. The workflow runs on `ubuntu-latest`, where `-printf` is available.

**Verify the redaction filter actually works.** This is the control that stops the diff job publishing private keys, so test it rather than trusting it:

```bash
kustomize build --enable-helm infrastructure/cilium > /tmp/cil.yaml
echo "base64 runs before: $(grep -cE '[A-Za-z0-9+/]{60,}' /tmp/cil.yaml)"
echo "base64 runs after:  $(sed -E 's/[A-Za-z0-9+\/]{60,}=*/<REDACTED>/g' /tmp/cil.yaml | grep -cE '[A-Za-z0-9+/]{60,}')"
```

Expected: a non-zero count before, and `0` after.

Do **not** try to spot-check this with `grep -A2 'kind: Secret'`. Kustomize emits each resource's top-level keys alphabetically, so `data` sorts before `kind` — after-context never captures the key material, and the check silently reports success regardless of whether redaction works.

- [ ] **Step 3: Commit and push on a branch, so the workflow exercises the PR path**

```bash
git checkout -b ci/validate-workflow
git add .github/workflows/validate.yaml
git commit -m "ci: validate and diff rendered manifests on every pull request

Discovers Kustomizations dynamically, renders each with --enable-helm,
schema-validates with kubeconform -strict against the CRDs catalog, and
posts a dyff of the rendered objects as a sticky PR comment."
git push -u origin ci/validate-workflow
```

- [ ] **Step 4: Open a pull request and verify all four jobs**

```bash
git log --oneline -1
```

Open the PR in the GitHub UI (or `gh pr create --fill` if you install `gh`).

Expected: `prepare` succeeds and lists four directories; `validate` runs four matrix legs, all green; `diff` posts a comment reading "No changes to rendered Kubernetes objects" (the workflow file changes no manifests); `lint` completes, possibly with findings, and does not block.

- [ ] **Step 5: Prove the diff job actually detects a change**

A diff job that never shows a diff is untested.

```bash
sed -i.bak 's/memory: 256Mi/memory: 320Mi/' infrastructure/argocd/values.yaml && rm infrastructure/argocd/values.yaml.bak
git commit -am "test: temporary values change to exercise the diff job"
git push
```

Expected: the sticky comment updates to show the `argocd-application-controller` memory request changing under `### infrastructure/argocd` — and also under `### bootstrap/argocd`, since it references the same directory.

Then revert:

```bash
git revert --no-edit HEAD
git push
```

Expected: the comment returns to "No changes to rendered Kubernetes objects."

- [ ] **Step 6: Merge**

```bash
git checkout main
git merge --ff-only ci/validate-workflow
git push
git branch -d ci/validate-workflow
```

---

## Task 10: Renovate

**Files:**
- Create: `.github/renovate.json5`

- [ ] **Step 1: Write the configuration**

`.github/renovate.json5`:

```json5
{
  $schema: "https://docs.renovatebot.com/renovate-schema.json",
  extends: [
    "config:recommended",
    ":dependencyDashboard",
    "helpers:pinGitHubActionDigests",
  ],

  timezone: "Europe/Berlin",
  schedule: ["* 0-6 * * 1"],
  prConcurrentLimit: 5,
  commitMessagePrefix: "chore(deps): ",

  packageRules: [
    {
      // Either of these can take the cluster down. Always a dedicated PR,
      // never automerged, and never batched with anything else.
      matchPackageNames: ["cilium", "argo-cd"],
      groupName: null,
      automerge: false,
      addLabels: ["critical-infrastructure"],
      prPriority: 10,
    },
    {
      // Everything else: patch bumps land on their own once CI is green.
      matchUpdateTypes: ["patch"],
      excludePackageNames: ["cilium", "argo-cd"],
      automerge: true,
      automergeType: "pr",
      platformAutomerge: true,
    },
    {
      matchUpdateTypes: ["major"],
      addLabels: ["major-update"],
      automerge: false,
    },
  ],

  customManagers: [
    {
      // Pinned CLI tool versions in workflow env: blocks, marked with a
      // "# renovate:" comment on the preceding line.
      customType: "regex",
      description: "CLI tool versions pinned in GitHub workflows",
      managerFilePatterns: ["/^\\.github/workflows/.+\\.ya?ml$/"],
      matchStrings: [
        "# renovate: datasource=(?<datasource>\\S+) depName=(?<depName>\\S+)\\s+[A-Z_]+_VERSION:\\s*(?<currentValue>\\S+)",
      ],
    },
  ],
}
```

- [ ] **Step 2: Commit and push**

```bash
git add .github/renovate.json5
git commit -m "ci: configure Renovate with automerge gated on CI

Cilium and ArgoCD always get dedicated, non-automerged PRs. Patch updates
elsewhere automerge once checks pass."
git push
```

- [ ] **Step 3: Install the Renovate GitHub App**

Go to `https://github.com/apps/renovate`, install it, and grant access to `alex-senger/homelab`.

- [ ] **Step 4: Verify Renovate parses the config and finds the dependencies**

Wait for the onboarding pull request (usually within minutes), then merge it. Renovate then opens a **Dependency Dashboard** issue.

```bash
open https://github.com/alex-senger/homelab/issues
```

Expected on the dashboard: `cilium` (1.18.0) and `argo-cd` from the `helmCharts` entries — proving the `kustomize` manager reads them natively — plus `kustomize`, `kubeconform` and `dyff` from the custom manager, plus the GitHub Actions.

If the three CLI tools are **absent**, the custom manager did not match. The likely cause is the `managerFilePatterns` key: Renovate renamed `fileMatch` to `managerFilePatterns` in v40, so an older bot needs `fileMatch: ["^\\.github/workflows/.+\\.ya?ml$"]` instead. Check the dashboard's config-warning section, switch the key, and push.

This does not block anything else — the chart versions are the ones that matter, and they come from the built-in manager.

- [ ] **Step 5: Confirm the critical-infrastructure rule is in force**

When Renovate opens its first Cilium or ArgoCD PR, verify it carries the `critical-infrastructure` label and is not set to automerge. If either is wrong, fix `packageRules` before the bot upgrades your CNI unattended.

---

## Task 11: Documentation

**Files:**
- Modify: `README.md`
- Create: `docs/bootstrap.md`, `docs/runbooks/cilium-recovery.md`, `docs/runbooks/argocd-recovery.md`

- [ ] **Step 1: Write the bootstrap document**

`docs/bootstrap.md`:

````markdown
# Bootstrap

How to bring the ArgoCD layer of the `roastery` cluster up from nothing.

## Prerequisites

- `kubectl` with a working context for `roastery`
- `kustomize` v5 (standalone — `kubectl apply -k` cannot inflate Helm charts)
- The repository must be **public**. ArgoCD clones over anonymous HTTPS; a private
  repository needs a credential Secret, which this cluster deliberately does not have.

## Bootstrap

```bash
kustomize build --enable-helm bootstrap/argocd | kubectl apply --server-side -f -
kubectl -n argocd rollout status deploy/argocd-server --timeout=300s
kubectl apply -f cluster/root.yaml
```

`--server-side` is required, not optional: ArgoCD's CRDs exceed the 262 144-byte
`last-applied-configuration` annotation that client-side apply writes. If the apply reports
field-manager conflicts from a previous installation, add `--force-conflicts`.

## Verify

```bash
kubectl -n argocd get applications
```

All Applications should reach `Synced` and `Healthy`.

## Admin access

```bash
kubectl -n argocd get secret argocd-initial-admin-secret \
  -o jsonpath='{.data.password}' | base64 -d; echo
kubectl -n argocd port-forward svc/argocd-server 8080:80
```

Log in at `http://localhost:8080` as `admin`. TLS terminates at the Gateway from
sub-project #3 onward; until then ArgoCD serves plaintext internally and is reached
by port-forward only.

## Why bootstrap and infrastructure cannot drift

`bootstrap/argocd/kustomization.yaml` contains nothing but a reference to
`infrastructure/argocd`. There is exactly one definition of ArgoCD in this repository, so the
usual self-management failure — a bootstrap manifest that diverges from the GitOps-managed one,
leaving the Application permanently `OutOfSync` — cannot occur.
````

- [ ] **Step 2: Write the Cilium recovery runbook**

`docs/runbooks/cilium-recovery.md`:

````markdown
# Runbook: Cilium is broken

## Symptoms

Pods stuck in `ContainerCreating`, DNS resolution failing cluster-wide, `cilium status`
reporting unready daemonsets, or Services unreachable from inside the cluster.

## Why you can always recover

The kube-apiserver runs on **host networking** at `192.168.178.16:6443`. It stays reachable with
Cilium completely broken, so `kubectl` and `helm` keep working from your laptop. `talosctl` does
not depend on the CNI either.

## Immediate recovery

Reinstall Cilium directly with Helm, bypassing ArgoCD:

```bash
helm repo add cilium https://helm.cilium.io
helm upgrade --install cilium cilium/cilium --version 1.18.0 \
  -n kube-system -f infrastructure/cilium/values.yaml
cilium status --wait
```

This leaves a Helm release object that ArgoCD does not know about. Once the cluster is healthy,
delete it so there is one source of truth again:

```bash
kubectl delete secret -n kube-system -l 'owner=helm,name=cilium'
```

## If a bad commit caused it

```bash
git revert <bad-sha>
git push
kubectl -n argocd patch application cilium --type merge \
  -p '{"operation":{"sync":{"revision":"main"}}}'
```

## If the Application was deleted

Cilium's Application deliberately carries no `resources-finalizer.argocd.argoproj.io`, so
deleting it does **not** delete the CNI. Recreate it:

```bash
kubectl apply -f cluster/applications/cilium.yaml
```

## Node-level inspection

```bash
talosctl -n 192.168.178.16 dmesg | tail -50
talosctl -n 192.168.178.16 services
talosctl -n 192.168.178.16 logs kubelet | tail -50
```

## Do not

Do not enable `prune: true` on Cilium's Application. Pruning a CNI resource because of a
transient render failure takes down cluster networking.

Do not add a finalizer to Cilium's Application.
````

- [ ] **Step 3: Write the ArgoCD recovery runbook**

`docs/runbooks/argocd-recovery.md`:

````markdown
# Runbook: ArgoCD is broken

## Symptoms

Applications stop reconciling, the repo-server crashlooping, ArgoCD `OutOfSync` against itself,
or a bad commit to `infrastructure/argocd` that broke the deployment that would fix it.

## Recovery: re-apply from Git

ArgoCD manages itself, but the bootstrap path never depends on ArgoCD running:

```bash
kustomize build --enable-helm bootstrap/argocd | kubectl apply --server-side -f -
kubectl -n argocd rollout status deploy/argocd-server --timeout=300s
```

If a bad commit is the cause, revert first so ArgoCD does not immediately re-break itself:

```bash
git revert <bad-sha>
git push
```

## Recovery: full reinstall

Safe as long as no Application carries a finalizer whose resources you cannot afford to lose.
Check first:

```bash
kubectl -n argocd get applications -o json \
  | python3 -c 'import json,sys; [print(a["metadata"]["name"], a["metadata"].get("finalizers")) for a in json.load(sys.stdin)["items"]]'
```

Deleting the namespace deletes the Applications. Any Application **with** a finalizer will
cascade-delete its managed resources. Cilium has no finalizer precisely so this is survivable.

```bash
kubectl delete namespace argocd --wait=true
kustomize build --enable-helm bootstrap/argocd | kubectl apply --server-side -f -
kubectl -n argocd rollout status deploy/argocd-server --timeout=300s
kubectl apply -f cluster/root.yaml
```

The Argo CRDs are cluster-scoped and survive namespace deletion.

## Symptom: permanently OutOfSync against itself

This means `bootstrap/argocd` and `infrastructure/argocd` render differently, which should be
impossible — `bootstrap/` is nothing but a reference. Verify:

```bash
kustomize build --enable-helm bootstrap/argocd > /tmp/a.yaml
kustomize build --enable-helm infrastructure/argocd > /tmp/b.yaml
dyff between /tmp/a.yaml /tmp/b.yaml
```

Expected: zero differences. Anything else means `bootstrap/argocd/kustomization.yaml` grew
content it should not have.

## Symptom: CRD apply fails with "annotation too long"

Client-side apply was used somewhere. Every apply of ArgoCD needs `--server-side`, and ArgoCD's
own Application needs `ServerSideApply=true` in its `syncOptions`.
````

- [ ] **Step 4: Write the README**

`README.md`:

````markdown
# homelab

GitOps configuration for `roastery`, a two-node Talos Linux Kubernetes cluster running on a
single Proxmox host. Everything in the cluster is declared here and reconciled by ArgoCD.

## Stack

| Layer | Choice |
|---|---|
| OS | Talos Linux v1.13.8 — immutable, API-managed, no SSH |
| Kubernetes | v1.36.2 |
| CNI | Cilium 1.18.0, kube-proxy replacement via eBPF, VXLAN, Hubble |
| GitOps | ArgoCD v3.5.x, app-of-apps |
| Rendering | Kustomize, with Helm charts inflated via `helmCharts:` |
| CI | GitHub Actions — render, `kubeconform -strict`, rendered-object diff on every PR |
| Updates | Renovate, automerge gated on CI |

## Layout

```
bootstrap/        applied by hand exactly once
cluster/          ArgoCD control plane: root Application, AppProjects, one Application per component
infrastructure/   platform component manifests
apps/             user-facing workloads
talos/            machine configuration
docs/             bootstrap, runbooks, ADRs, specs and plans
```

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

This is a learning cluster, and the README should say what it is rather than what it resembles.

**High availability is practised here, not achieved.** Both nodes are VMs on one Proxmox host
with one power supply. Replicated storage places every replica on the same NVMe. What replication
genuinely buys is surviving *node* reboots, which matters because Talos upgrades are frequent —
not surviving hardware failure. The mitigation that does help is placing the storage backup target
on a physically separate SATA disk.

**The host is modest.** An Intel i5-3550S: four threads shared between the Talos nodes and a
Debian VM. It works. It is not fast, and Prometheus scrape intervals are tuned accordingly.

**One control-plane node.** etcd has no quorum, so a control-plane reboot is an API outage. A
third node is planned.

**Cilium's CRDs are not declarative.** The Cilium chart ships no CustomResourceDefinition
manifests; the agent and operator register the ten `cilium.io` CRDs programmatically at startup.
Everything else about the CNI is declared here, but those ten resources exist because Cilium put
them there, not because this repository asked for them.

## Design documents

- [Specs](docs/superpowers/specs/) — what is being built and why
- [ADRs](docs/decisions/) — decisions and their consequences
- [Runbooks](docs/runbooks/) — what to do when it breaks
````

- [ ] **Step 5: Verify every internal link resolves**

```bash
cd ~/workspaces/homelab
for f in docs/bootstrap.md docs/runbooks/cilium-recovery.md docs/runbooks/argocd-recovery.md docs/decisions docs/superpowers/specs; do
  test -e "$f" && echo "OK   $f" || echo "MISS $f"
done
```

Expected: all `OK`.

- [ ] **Step 6: Commit**

```bash
git add README.md docs/bootstrap.md docs/runbooks
git commit -m "docs: add README, bootstrap guide and recovery runbooks

README states the single-host HA caveat plainly rather than implying
production-grade redundancy."
git push
```

---

## Task 12: Final verification against the spec

**Files:** none.

Every check here is named in the spec's definition of done (§15).

- [ ] **Step 1: All Applications synced and healthy**

```bash
kubectl -n argocd get applications
```

Expected: `root`, `argocd`, `cilium` — all `Synced`, all `Healthy`.

- [ ] **Step 2: Single source of truth**

```bash
helm list -A
```

Expected: empty.

- [ ] **Step 3: Cluster healthy**

```bash
cilium status --wait
kubectl get pods -A | grep -vE 'Running|Completed' || echo "all pods healthy"
```

- [ ] **Step 4: Drift test, one more time**

```bash
kubectl -n argocd scale deploy/argocd-repo-server --replicas=2
sleep 45
kubectl -n argocd get deploy argocd-repo-server -o jsonpath='{.spec.replicas}'; echo
```

Expected: `1`.

- [ ] **Step 5: Rebuild-from-clone works**

The strongest claim this repository makes is that a fresh clone reproduces the cluster. Test it.

```bash
cd $(mktemp -d)
git clone https://github.com/alex-senger/homelab.git
cd homelab
kustomize build --enable-helm bootstrap/argocd | kubectl diff --server-side -f - || true
```

Expected: `kubectl diff` reports no changes, or only immaterial ones. A material difference means live state has drifted from the repository.

- [ ] **Step 6: CI is green on main**

```bash
open https://github.com/alex-senger/homelab/actions
```

Expected: the latest `validate` run on `main` is green.

- [ ] **Step 7: Renovate is active**

Expected: a Dependency Dashboard issue exists, listing `cilium` and `argo-cd` among tracked dependencies.

- [ ] **Step 8: Record completion**

```bash
git log --oneline | head -20
```

Expected: a readable sequence of Conventional Commits telling the story from scaffold to verified cluster.

---

## What is deliberately not here

Named so a reviewer knows it is a decision, not an omission:

- **Secrets of any kind.** Sub-project #1 introduces zero secret material, which is what makes the repository safe to be public from its first commit. Secrets arrive in sub-project #4 via External Secrets Operator and OpenBao.
- **Storage.** No StorageClass, no Longhorn. Sub-project #3.
- **Ingress.** No Gateway API, no cert-manager, no `CiliumLoadBalancerIPPool`, no Tailscale subnet router. A `type: LoadBalancer` Service will sit in `<pending>` until sub-project #3.
- **Talos machine configuration.** `talos/` is empty. Sub-project #2 — which must also persist the control-plane untaint as `cluster.allowSchedulingOnControlPlanes: true`, because right now that exists only as live cluster state and vanishes on a node rebuild.
- **Observability.** Sub-project #5.
- **Applications.** `apps/` is empty. Sub-project #6.

# Observability (VictoriaMetrics + Grafana) — bring-up & recovery

Sub-project #6. Component: `infrastructure/monitoring` (ArgoCD app `monitoring`,
sync-wave 20). Chart: victoria-metrics-k8s-stack 0.92.1.

## One-time bring-up (operator steps, after the branch is merged)

1. **Create the Grafana admin credential in OpenBao** (values are never in Git):

       kubectl exec -n openbao openbao-0 -- sh -c \
         'BAO_TOKEN=<root-or-admin-token> bao kv put secret/monitoring/grafana-admin \
            admin-user=admin admin-password=<choose-a-strong-password>'

   The `ExternalSecret` `grafana-admin` (ns `monitoring`) then materialises the
   `grafana-admin` Secret with keys `admin-user` / `admin-password`, which Grafana
   reads via `admin.existingSecret`.

2. **Roll Cilium so Hubble metrics activate** (config-checksum gotcha — see
   cilium-recovery.md). ArgoCD updates `cilium-config` but does not restart the
   agents:

       kubectl -n kube-system rollout restart deployment/cilium-operator daemonset/cilium

3. Let ArgoCD sync `monitoring` (or `kubectl -n argocd annotate app monitoring
   argocd.argoproj.io/refresh=hard --overwrite` if it lags a reconcile behind
   the merge — see the sub-project #5 lesson).

## Acceptance checks

- Pods Running: `kubectl -n monitoring get pods` — VMSingle, VMAgent, Grafana,
  kube-state-metrics up; node-exporter on all three nodes.
- Grafana on the LAN: `kubectl -n monitoring get svc` shows the Grafana Service
  with EXTERNAL-IP `192.168.178.202`; browse `http://192.168.178.202` and log in
  with the OpenBao-sourced admin credential (this proves the ESO path).
- VMAgent targets healthy: port-forward VMSingle
  (`kubectl -n monitoring port-forward svc/vmsingle-vm 8429:8429`) — or use the
  VMAgent `/targets` page — and confirm `up` for kube-apiserver, kubelet,
  cAdvisor, node-exporter, kube-state-metrics, coredns, cert-manager,
  cilium-envoy, and hubble.
- A dashboard shows real node CPU/memory and Hubble flow data.

## Known ArgoCD behavior (syncJob immutability)

The victoria-metrics-k8s-stack chart's `syncJob` (which fetches Grafana dashboards
and alert rules at deploy time) is rendered as a Kubernetes Job carrying Helm hook
annotations (`helm.sh/hook: post-install,post-upgrade`). Because the `monitoring`
Application applies the chart via kustomize and direct manifests — not ArgoCD's native
Helm source — ArgoCD does not interpret these Helm hook annotations and applies the
Job as an ordinary resource instead.

**Effect:** on later ArgoCD reconciles, the `monitoring` Application may show the
sync-job as `OutOfSync` or report an immutable-field conflict on the Job itself. This
is purely cosmetic — dashboards and rules still load correctly at the initial deploy.

**Handling (optional):** if the sync-job OutOfSync state becomes noisy, either:
- Add the sync-job to the Application's `ignoreDifferences` policy, or
- Set `syncJob.enabled: false` in `infrastructure/monitoring/values.yaml` and
  provide dashboards through another mechanism (e.g., ConfigMap-based provisioning).

Note this as a follow-up optimization only, not a required bring-up step.

## Deferred scrapes (follow-ups, each needs a change in that component's own app)

- **external-secrets** — no metrics Service today; enable the ESO chart's metrics
  service in `infrastructure/external-secrets/values.yaml`, then add a
  VMServiceScrape.
- **ArgoCD** — enable per-component `metrics.enabled` in
  `infrastructure/argocd/values.yaml` (controller/server/repoServer), then scrape.
- **OpenBao** — exposes Prometheus telemetry only with
  `telemetry.unauthenticated_metrics_access` or a scoped token; a deliberate
  security decision, deferred.
- **Longhorn** — deferred. The `longhorn-backend` Service port 9500 (`manager`)
  is the longhorn-manager API bound to the pod IP, not a Prometheus endpoint — a
  scrape of `:9500/metrics` hangs (verified 2026-09-10, longhorn-manager v1.12.1).
  Revisit with the correct Longhorn metrics endpoint before re-adding a scrape.

## Bring-up notes (2026-09-10)

- **node-exporter needs the `privileged` PodSecurity label.** Talos enforces
  `baseline` cluster-wide; node-exporter's hostNetwork/hostPID/hostPath/hostPort
  are rejected under it, leaving the DaemonSet at DESIRED n / CURRENT 0. The
  `monitoring` namespace therefore carries
  `pod-security.kubernetes.io/enforce: privileged`
  (`infrastructure/monitoring/namespace.yaml`), same as longhorn-system.
- **VM operator admission-webhook drift is ignored, not fought.** The operator
  injects its own CA into `vm-victoria-metrics-operator-admission` and populates
  its validation Secret at runtime, so both drift from the rendered manifests.
  `cluster/applications/monitoring.yaml` lists them under `ignoreDifferences`
  (webhook `caBundle` + the Secret's `/data`); without it the app stays
  OutOfSync and selfHeal fights the operator.

## Failure modes

- VMSingle/VMAgent/Grafana down → metrics gap only; no workload impact; ArgoCD
  selfHeal restarts them.
- Metrics PVC fills → bounded by 30d retention + 10Gi; raise size or shorten
  retention in `infrastructure/monitoring/values.yaml`.
- Hubble metrics missing after merge → the rollout restart in step 2 was not run.
- Grafana pod fails to start with `CreateContainerConfigError` → the OpenBao key
  from step 1 is missing, so the `ExternalSecret` never created the
  `grafana-admin` Secret. Confirm step 1 ran and check that the `ExternalSecret`
  is `SecretSynced` (`kubectl -n monitoring get externalsecret`).

# Observability (VictoriaMetrics + Grafana) — bring-up & recovery

#6. `infrastructure/monitoring` (app `monitoring`, wave 20), chart victoria-metrics-k8s-stack 0.92.1.

## Bring-up (after merge)
1. Seed the Grafana admin in OpenBao (never in Git):

       kubectl exec -n openbao openbao-0 -- sh -c \
         'BAO_TOKEN=<root> bao kv put secret/monitoring/grafana-admin admin-user=admin admin-password=<strong>'

   → ESO materialises `grafana-admin` (Grafana `admin.existingSecret`).
2. Roll Cilium so Hubble metrics activate (config-checksum gotcha — cilium-recovery.md):

       kubectl -n kube-system rollout restart deployment/cilium-operator daemonset/cilium

3. Let ArgoCD sync `monitoring` (hard-refresh if it lags a reconcile).

## Verify
- Pods Running: VMSingle, VMAgent, Grafana, kube-state-metrics, node-exporter.
- Grafana: LB `192.168.178.4` (direct/local-admin break-glass) or `https://grafana.senger-solutions.com`
  (Authelia SSO).
- VMAgent `/targets` all `up`: kube-apiserver, kubelet, cAdvisor, node-exporter, kube-state-metrics,
  coredns, cert-manager, cilium-envoy, hubble.

## Notes
- node-exporter needs the `privileged` PodSecurity label (Talos baseline rejects its
  hostPath/hostPort) — set on the `monitoring` namespace.
- The VM operator mutates its admission-webhook caBundle + validation Secret at runtime →
  `cluster/applications/monitoring.yaml` `ignoreDifferences` (else selfHeal fights it).
- The chart's `syncJob` (dashboard fetch) is a Helm-hook Job applied as a plain resource → may show
  cosmetically OutOfSync; harmless.
- Deferred scrapes (each needs metrics enabled in its own component): external-secrets, ArgoCD,
  OpenBao (needs unauthenticated telemetry), Longhorn (`longhorn-backend:9500` is the manager API,
  not a Prometheus endpoint — a scrape hangs).

## Failure modes
- VM*/Grafana down → metrics gap only; selfHeal restarts them.
- Metrics PVC fills → bounded by 30d retention / 10Gi; adjust in values.
- Hubble metrics missing after merge → the step-2 rollout wasn't run.
- Grafana `CreateContainerConfigError` → `grafana-admin` OpenBao key missing (step 1); check the
  ExternalSecret is SecretSynced.

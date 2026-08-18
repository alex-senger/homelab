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

`kustomize build --enable-helm <dir>` on a laptop, in GitHub Actions, and in the ArgoCD repo-server
produce identical output. CI therefore validates and diffs the real objects rather than
approximating them with a separate `helm template` invocation that may drift.

Chart output stays patchable. Adding an `HTTPRoute` or `ExternalSecret` beside a chart is another
entry in the same Kustomization rather than a second Application source.

The costs: `--enable-helm` must be enabled on the repo-server, charts are re-downloaded on each
render (mitigated by caching `~/.cache/helm` in CI), and `helm rollback` is no longer available —
rollback is `git revert`.

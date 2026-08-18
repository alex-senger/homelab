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

One explicit `Application` per component, all in `cluster/applications/`.

## Consequences

Adding a component costs roughly fifteen lines of boilerplate.

In exchange, sync ordering is one annotation on one file, and the deployment order of the whole
cluster is readable by listing a single directory. Per-component overrides — `ignoreDifferences`,
sync options, finalizer presence — sit next to the component they affect.

Achieving the same with generators requires a second layer: a `config.yaml` per component read by
a git-files generator. That reproduces this decision's explicitness with more indirection, and
turns "why did this not sync" into debugging a generator rather than reading a file.

Revisit if the component count exceeds roughly thirty, where boilerplate starts to dominate.

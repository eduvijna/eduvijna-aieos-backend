---
id: PED-I04-CI-VERIFIED-BUILD-CONTRACT
title: CI quality gates and immutable verified build bundle foundation
status: draft
version: 1.0.0
---

# PED-I04 CI verified build contract

ADR-AIEOS-029 is frozen. PED-I01–PED-I03 remain in force.

PED-I04 implements the **CI quality-gate + immutable NON_PRODUCTION verified
Python build bundle** foundation only.

It does **not** authorize production deployment, mutation, or migration.

## Workflow

Path: `.github/workflows/ci.yml`

| Trigger | Jobs |
|---------|------|
| `pull_request` | `quality-gate` only |
| `push` to `main` | `quality-gate` → `verified-build` |

Permissions: `contents: read` only. No secrets. No `pull_request_target`.
Actions are pinned by full commit SHA. Checkout uses `persist-credentials: false`.

Toolchain pins:

- Python `3.14.7`
- uv `0.12.4`

## quality-gate

Stable required-check name for future branch protection:

`quality-gate`

Gates:

1. `uv lock --check`
2. `uv sync --locked --group dev`
3. `python -m compileall -q src tests migrations tools`
4. full `pytest -v` (PostgreSQL 18 via existing ephemeral fixture strategy)

## verified-build

Runs only after `quality-gate` succeeds on `push` to `main`.

Produces one immutable tar bundle:

`aieos-<version>-<full-git-sha>.tar`

containing wheel, sdist, verified-build-manifest.json, OpenAPI snapshot, and
`uv.lock`. Classification:

`NON_PRODUCTION`

`production_authorized`, `deployable`, and `mutation_authorized` are false.

Bundle SHA-256 is the immutable identity of this PED-I04 artefact. It is **not**
automatically mapped to production `AIEOS_ARTIFACT_DIGEST`.

## Branch protection (future governance)

Future production governance **must** require the GitHub check:

`quality-gate`

on protected `main` before any production authorization.

PED-I04 **does not claim** branch protection is already enabled, and does not
mutate repository branch-protection settings.

## Explicit non-goals

- production deployment / OCI image / Dockerfile / GHCR / PyPI / GitHub Release
- ASGI server / process entrypoint
- mutation activation changes (PED-I03 unchanged)
- production migration
- SBOM / attestation hard dependency
- feature-flag platforms

## Authorization status

- production deployment remains **NOT AUTHORIZED**
- production mutation remains **NOT AUTHORIZED**
- production migration remains **NOT AUTHORIZED**

PED-I05+ remains **NOT AUTHORIZED** until separately gated.

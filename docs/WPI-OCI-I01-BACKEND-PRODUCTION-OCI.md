# WPI-OCI-I01 — Backend Production OCI Source and Provenance Validation

## Authority

Governed by **ADR-AIEOS-051** (Frozen / Approved) after Architecture merge
`b153193256450d0ce8afe0b5d2127dfbfd8f2123`.

This work item adds **source + credential-free offline/CI validation only**.

## Artifact role

One common Backend OCI image candidate under:

- registry (future): `eduvijna-registry`
- repository (future): `aieos-backend`

Consumers (same immutable digest after future publication):

- WORKFLOW_DISPATCHER — `python -m aieos.platform.runtime.entrypoints.workflow_dispatcher_main`
- TEMPORAL_WORKER — `python -m aieos.platform.runtime.entrypoints.temporal_worker_main`

API runtime and EVENT dispatcher are **not** part of this first-production OCI workload slice.

## Source-identity transition (critical)

```text
8f4dd172e6a0ba8b4ad944b0ae22060442356342
= WPI-OCI-I01 implementation BASE only
```

That SHA must **not** be hard-coded as the future published image revision.

- PR validation builds using the **exact PR commit SHA**
- Post-merge validation uses the **exact merge commit SHA**
- Future **WPI-OCI-P01** publication authority is the exact reviewed **post-I01 Backend main SHA**
- Future convenience source tag:

```text
git-<POST_I01_MERGED_BACKEND_SHA>
```

Do not mix the new Dockerfile/tooling with the old base SHA as published image identity.

## Production Dockerfile

Path:

```text
deploy/oci/Dockerfile.backend-runtime
```

Posture:

- Python **3.14.7**
- uv **0.12.4**
- platform **linux/amd64**
- immutable digest-pinned base:
  `ghcr.io/astral-sh/uv:0.12.4-trixie-slim@sha256:8d033111899301598e33bd321b85f33f86e3ba2953ce00ff70a9cac020246a7c`
- `uv sync --locked --no-dev --no-editable`
- non-root **UID/GID 10001** (`USER 10001:10001`)
- OCI identity labels including Architecture/Infrastructure revision pins
- classification label `PRODUCTION_BACKEND_RUNTIME` = artifact **purpose** only (not published/deployed)

## Runtime probe remains NON_PRODUCTION

```text
deploy/oci/Dockerfile.api-runtime-probe
```

remains **NON_PRODUCTION_RUNTIME_PROBE**, unchanged, not renamed, not promoted, not production authority.

## Fail-closed default command

Default image execution:

- prints `AIEOS_BACKEND_RUNTIME_COMMAND_REQUIRED`
- exits **64**
- does not start dispatcher/worker/API/EVENT
- does not open ports / connect to DB/NATS/Temporal/AIStor

App Platform must supply the governed worker run command.

## Provenance (pre-publication)

Tools (stdlib helpers; separate from PED-I04 NON_PRODUCTION bundle):

- `tools/release/backend_oci_common.py`
- `tools/release/build_backend_oci_provenance.py`
- `tools/release/verify_backend_oci_provenance.py`

Schema:

- `artifact_kind = AIEOS_BACKEND_PRODUCTION_OCI_PROVENANCE`
- `classification = PRODUCTION_RUNTIME_CANDIDATE`
- `publication_performed = false`
- `publication_authorized = false`
- `deployment_authorized = false`

`image_config_id` is **local Docker/OCI config identity**, never registry manifest digest authority.

Pre-publication receipts **must not** claim registry/repository/manifest publication fields.

## Credential-free validation

```text
tools/runtime/run_backend_oci_validation.sh
```

CI job: `backend-production-oci`

Proves local `linux/amd64` build, Python/uv, UID/GID, both worker imports, labels, fail-closed default, sanitized receipt verify.

**Does not:**

- `docker login` / `docker push`
- DigitalOcean API / `doctl`
- GitHub Environment / registry secrets
- upload OCI image or receipt as Actions artifacts

## Future gates (not authorized here)

| Gate | Status |
|------|--------|
| WPI-OCI-I01 | THIS — source + offline/CI only |
| WPI-OCI-P01 live first publication | **NOT AUTHORIZED** |
| Future publication credential (`registry:read` + `registry:update` only) | **NOT AUTHORIZED** |
| WPI-AP-DP-TV01 | **AUTHORIZED BUT PAUSED ON OCI MANIFEST DIGEST** |
| Production App Platform deployment | **NOT AUTHORIZED** |

Source-SHA convenience tags are **not** deployment authority. Production/runtime authority is only an immutable `sha256:` registry manifest digest after separately authorized P01 read-back reconciliation.

---
id: PED-I01-PRODUCTION-RUNTIME-CONFIG-CONTRACT
title: Production/staging API runtime configuration and composition foundation
status: draft
version: 1.0.0
---

# PED-I01 production runtime configuration contract

ADR-AIEOS-029 (Production Environment & Deployment Readiness Baseline) is frozen.

PED-I01 implements the **configuration / composition foundation only** for the
STAGING and PRODUCTION API workload.

## What this slice provides

- Typed fail-closed `ApiRuntimeConfig` loaded from exact environment variable names
- Immutable `ReleaseIdentity` (full 40-char Git SHA + `sha256:` artifact digest)
- Workload kind vocabulary (`API`, dispatchers, Temporal worker, content migration)
- Role-name grammar and role-separation validation (runtime ≠ owners ≠ migrator)
- Rejection of migrator DSN (`AIEOS_DATABASE_URL`) in the API runtime environment
- Secret-safe `repr`/`str` for configuration (DB password and cursor key redacted)
- Explicit `ApiRuntimeDependencies` + `compose_api_application(...)` wrapping
  existing `create_app(...)` (no permissive production adapters)

## Exact API runtime environment variables

| Variable | Purpose |
|----------|---------|
| `AIEOS_DEPLOYMENT_ENVIRONMENT` | `STAGING` or `PRODUCTION` only |
| `AIEOS_RELEASE_VERSION` | Application version |
| `AIEOS_GIT_SHA` | Full 40-char lowercase hex Git SHA |
| `AIEOS_BUILD_ID` | Build identifier |
| `AIEOS_ARTIFACT_DIGEST` | `sha256:<64 lowercase hex>` |
| `AIEOS_RUNTIME_DATABASE_URL` | Runtime DB URL (secret; not echoed in errors). **Must** use exact driver `postgresql+psycopg://` (Psycopg 3). Bare `postgresql://` and other dialects/drivers are rejected. |
| `AIEOS_RUNTIME_DATABASE_ROLE` | Runtime role; must match URL username |
| `AIEOS_SCHEMA_OWNER_ROLE` | Content schema owner (not runtime) |
| `AIEOS_SECURITY_SCHEMA_OWNER_ROLE` | Security schema owner (not runtime) |
| `AIEOS_MIGRATOR_ROLE` | Migrator role name (not runtime login) |
| `AIEOS_CURSOR_SIGNING_KEY_B64` | Base64 cursor signing key (no default) |
| `AIEOS_IDEMPOTENCY_RETENTION_SECONDS` | Positive integer seconds |
| `AIEOS_RUNTIME_DATABASE_CONNECT_TIMEOUT_SECONDS` | Positive integer seconds for Psycopg connect timeout (PED-I02; required for STAGING/PRODUCTION; no silent default) |
| `AIEOS_AUTH_ISSUER` | Exact JWT issuer (PED-I08 / ADR-AIEOS-030; no default) |
| `AIEOS_AUTH_AUDIENCE` | Exact JWT audience (PED-I08 / ADR-AIEOS-030; no default) |
| `AIEOS_AUTH_JWKS_URI` | Absolute HTTPS JWKS URI (PED-I08 / ADR-AIEOS-030; no default) |

`AIEOS_DATABASE_URL` remains the Alembic migrator DSN and **must not** be present
in STAGING/PRODUCTION API runtime environments.

Local/ephemeral `.env.example` retains its non-production warning and is **not**
a production secret template. Production systems inject the names above without
storing real values in this repository.

## Explicit non-goals (this slice)

PED-I01 does **not** provide:

- a real production `SecurityContextResolver` / identity adapter
- production authorization or governance adapters (no Allow* defaults)
- a production SQLAlchemy Engine / pool / TLS / `current_user` readiness check
- an ASGI server (uvicorn/gunicorn/etc.) or process entrypoint
- `/livez` / `/readyz` health endpoints
- mutation activation switches or feature flags
- CI/CD workflows, containers, Kubernetes/Helm/Terraform, or hosting manifests
- NATS or Temporal requirements on the API config surface
- database migrations (head remains `saii020001`)
- OpenAPI / HTTP contract changes

## Authorization status

- production deployment remains **NOT AUTHORIZED**
- production mutation remains **NOT AUTHORIZED**
- production migration remains **NOT AUTHORIZED**

PED-I01 is a production-readiness **foundation** only.

Engine construction, identity/schema readiness, and `/livez`/`/readyz` are
advanced by **PED-I02** (see `docs/PED-I02-API-DB-READINESS-CONTRACT.md`).
PED-I03+ remains **NOT AUTHORIZED** until separately gated.

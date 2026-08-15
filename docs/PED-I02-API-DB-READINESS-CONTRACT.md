---
id: PED-I02-API-DB-READINESS-CONTRACT
title: API runtime database engine and readiness foundation
status: draft
version: 1.0.0
---

# PED-I02 API DB readiness contract

ADR-AIEOS-029 (Production Environment & Deployment Readiness Baseline) is frozen.
PED-I01 / PED-I01R1 runtime configuration composition remains in force.

PED-I02 implements the **API runtime database / readiness foundation only**.

It is classified as an API runtime database/readiness foundation.
It does **not** authorize production deployment, mutation, or migration.

## What this slice provides

- `create_api_runtime_engine(config)` — shared SQLAlchemy Engine using
  `postgresql+psycopg` and `AIEOS_RUNTIME_DATABASE_URL`
- Bounded connect timeout via `AIEOS_RUNTIME_DATABASE_CONNECT_TIMEOUT_SECONDS`
  (required positive integer; no silent default)
- `SqlAlchemyApiReadinessProbe` sharing that Engine with business UoW use
- Operational `GET /livez` and `GET /readyz` (`include_in_schema=False`)
- Non-secret release identity exposure on health responses

## Engine construction

- `pool_pre_ping=True`
- `hide_parameters=True`
- Psycopg `connect_timeout` from config
- Engine construction does **not** open a connection
- One Engine serves UoW factory + readiness (no privileged readiness Engine)
- Never reads `AIEOS_DATABASE_URL` / migrator credentials
- No AUTOCOMMIT for business work, no migrations, no `SET ROLE`, no tenant SET

## Readiness checks (fail-closed)

On each `/readyz` probe connection:

1. `current_user` and `session_user` equal `AIEOS_RUNTIME_DATABASE_ROLE`
2. `current_database()` equals the database named in the runtime DSN
3. Role attributes: login allowed; not superuser / createdb / createrole /
   replication / bypassrls
4. Not a member of content schema owner, security schema owner, or migrator
5. No `CREATE` on the current database
6. No `CREATE` on schemas `content`, `api`, `workflow`, `integration`, `security`
7. Schema owners match config (content/api/workflow/integration → content owner;
   security → security owner); runtime owns none of those schemas
8. PostgreSQL major version **18** only
9. Exactly one `public.alembic_version` row equal to `saii020001`

Governed codes only (no raw exception text): `READY`,
`DATABASE_UNAVAILABLE`, `DATABASE_IDENTITY_MISMATCH`, `DATABASE_ROLE_UNSAFE`,
`DATABASE_ROLE_MEMBERSHIP_UNSAFE`, `DATABASE_SCHEMA_OWNER_MISMATCH`,
`DATABASE_SCHEMA_REVISION_MISMATCH`, `DATABASE_SCHEMA_REVISION_UNAVAILABLE`,
`DATABASE_VERSION_MISMATCH`.

## Health endpoints

| Route | Behavior |
|-------|----------|
| `GET /livez` | Process liveness only. HTTP 200. Does **not** call PostgreSQL, readiness, SecurityContextResolver, NATS, Temporal, or AI. |
| `GET /readyz` | Invokes readiness probe every request. Ready → 200; not ready → 503. |

Both expose non-secret release identity (`application_version`, `git_sha`,
`build_id`, `artifact_digest`) and deployment environment. No passwords, DSN,
or cursor keys.

Health routes are operational and excluded from product OpenAPI.

## What readiness does **not** require

- NATS (unavailable NATS does not make API `/readyz` not ready)
- Temporal (unavailable Temporal does not make API `/readyz` not ready)
- AI provider
- Tenant / principal business context (`aieos.tenant_id` is not set)

## Privilege note

API runtime may have `USAGE` on schema `public` and `SELECT` on
`public.alembic_version` for migration-head readiness metadata only.

That grant is **not** migration authority: no Alembic execution, DDL,
`SET ROLE`, schema ownership, or write on `alembic_version`.

## TLS / network boundary

PED-I02 does **not** claim target production TLS, CA trust, network policy, or
private connectivity validation. Those remain target-environment PED-G15 gates.

## Explicit non-goals

- ASGI server / process entrypoint / module-level production `app`
- Mutation activation (`AIEOS_MUTATIONS_ENABLED`) or readiness-as-mutation-gate
- Pool sizing / autoscaling env contracts
- NATS or Temporal fields on `ApiRuntimeConfig`
- CI/CD, containers, Kubernetes/Helm/Terraform, hosting manifests
- New Alembic migration (head remains `saii020001`)
- Production role/credential provisioning

## Authorization status

- production deployment remains **NOT AUTHORIZED**
- production mutation remains **NOT AUTHORIZED**
- production migration remains **NOT AUTHORIZED**

PED-I03+ remains **NOT AUTHORIZED** until separately gated.

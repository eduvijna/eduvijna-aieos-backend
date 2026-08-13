---
id: GCI-I02-DATABASE-PRIVILEGE-CONTRACT
title: Generic Content and API infrastructure database privilege contract
status: draft
version: 0.4.0
---

# Database privilege contract (GCI-I02R2 / GCI-I05R1)

This document describes the required production privilege separation. It does **not** provision cloud or production identities.

ADR-AIEOS-024: runtime identity ≠ migration identity ≠ schema owner ≠ backup/restore authority.

Production role creation remains infrastructure/deployment work, not application migration work. Alembic must not `CREATE ROLE` or store production credentials.

## Identities

| Identity | Purpose | Login | Must not have |
|----------|---------|-------|----------------|
| Schema owner (`aieos_content_owner` conceptually) | Owns `content` and `api` schema objects | **NOLOGIN** | Application runtime login |
| Migrator | Alembic upgrade/downgrade only; may `SET ROLE` to schema owner for DDL | LOGIN | Product runtime DML path; `BYPASSRLS` as a substitute for application tenancy; schema ownership of `content` or `api` objects |
| Runtime | Ordinary Generic Content DML and synchronous API idempotency DML | LOGIN | Superuser, `BYPASSRLS`, schema ownership, `DELETE` on `content.contents`, `UPDATE`/`DELETE` on `content.content_versions`, `UPDATE`/`DELETE` on `api.idempotency_records` |
| Backup/restore | Backup and restore | deployment-defined | Ordinary product DML |

A runtime identity must **not** require `BYPASSRLS`, superuser, or schema ownership.

## Alembic role assumption

Alembic connects as the **migrator** identity and executes Generic Content and API infrastructure DDL only after an explicit:

```text
AIEOS_SCHEMA_OWNER_ROLE=<schema-owner-role>
```

The migration environment issues `SET LOCAL ROLE` to that role. Online migrations execute it on the migrator connection. Offline SQL generation emits the same `SET LOCAL ROLE` inside the migration transaction before `content` and `api` DDL, so generated SQL preserves migrator identity ≠ object owner when executed. It does **not** silently create `content` or `api` objects as the migrator. The named schema-owner role must already exist; Alembic does not `CREATE ROLE` for production.

After upgrade:

- `content` schema owner == schema-owner role
- `content.contents` owner == schema-owner role
- `content.content_versions` owner == schema-owner role
- `api` schema owner == schema-owner role
- `api.idempotency_records` owner == schema-owner role
- session migration identity (`session_user`) remains the migrator
- runtime identity ≠ schema owner ≠ migrator

## Runtime grants (logical)

### `content`

- `CONNECT` on the application database
- `USAGE` on schema `content`
- `content.contents`: `SELECT`, `INSERT`, `UPDATE` — **not** `DELETE`
- `content.content_versions`: `SELECT`, `INSERT` — **not** `UPDATE`, **not** `DELETE`
- `EXECUTE` on `content.current_tenant_id()`

Physical Content purge remains governed future lifecycle work. Ordinary Generic Content runtime has no purge authority.

`content.content_versions` UPDATE/DELETE remain additionally blocked by immutability triggers. Triggers are not a substitute for withholding those privileges from runtime.

### `api` (GCI-I05 synchronous idempotency state)

`api.idempotency_records` is platform/API retry state, not Generic Content business authority.

- `USAGE` on schema `api`
- `api.idempotency_records`: `SELECT`, `INSERT` — **not** `UPDATE`, **not** `DELETE`
- `EXECUTE` on `api.current_tenant_id()`
- runtime is **NOSUPERUSER**, **NOBYPASSRLS**, and not the schema owner

`api.idempotency_records` has ENABLE RLS and FORCE RLS. Tenant isolation uses transaction-local `aieos.tenant_id` via `api.current_tenant_id()`. Missing tenant context must fail closed. Session-persistent `SET` is not an application security mechanism.

Runtime must not receive UPDATE or DELETE on established idempotency outcomes. Retention cleanup is not a runtime privilege in this contract.

## Tenant context

Tenant context is transaction-local (`SET LOCAL` / `set_config(..., is_local := true)` for `aieos.tenant_id`) for both `content` and `api` policies.

## Out of scope

Cluster-level production role provisioning, secrets, deployment of these identities, and physical purge authorization.

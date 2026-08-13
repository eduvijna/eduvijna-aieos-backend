---
id: GCI-I02-DATABASE-PRIVILEGE-CONTRACT
title: Generic Content database privilege contract
status: draft
version: 0.2.0
---

# Generic Content database privilege contract (GCI-I02R1)

This document describes the required production privilege separation. It does **not** provision cloud or production identities.

ADR-AIEOS-024: runtime identity ≠ migration identity ≠ schema owner ≠ backup/restore authority.

Production role creation remains infrastructure/deployment work, not application migration work.

## Identities

| Identity | Purpose | Login | Must not have |
|----------|---------|-------|----------------|
| Schema owner (`aieos_content_owner` conceptually) | Owns `content` schema objects | **NOLOGIN** | Application runtime login |
| Migrator | Alembic upgrade/downgrade only; may `SET ROLE` to schema owner for DDL | LOGIN | Product runtime DML path; `BYPASSRLS` as a substitute for application tenancy; schema ownership of `content` objects |
| Runtime | Ordinary Generic Content DML | LOGIN | Superuser, `BYPASSRLS`, schema ownership, `DELETE` on `content.contents`, `UPDATE`/`DELETE` on `content.content_versions` |
| Backup/restore | Backup and restore | deployment-defined | Ordinary product DML |

A runtime identity must **not** require `BYPASSRLS`, superuser, or schema ownership.

## Alembic role assumption

Alembic connects as the **migrator** identity and executes Generic Content DDL only after an explicit:

```text
AIEOS_SCHEMA_OWNER_ROLE=<schema-owner-role>
```

The migration environment issues `SET LOCAL ROLE` to that role. It does **not** silently create `content` objects as the migrator. The named schema-owner role must already exist; Alembic does not `CREATE ROLE` for production.

After upgrade:

- `content` schema owner == schema-owner role
- `content.contents` owner == schema-owner role
- `content.content_versions` owner == schema-owner role
- session migration identity (`session_user`) remains the migrator
- runtime identity ≠ schema owner ≠ migrator

## Runtime grants (logical)

- `CONNECT` on the application database
- `USAGE` on schema `content`
- `content.contents`: `SELECT`, `INSERT`, `UPDATE` — **not** `DELETE`
- `content.content_versions`: `SELECT`, `INSERT` — **not** `UPDATE`, **not** `DELETE`
- `EXECUTE` on `content.current_tenant_id()`

Physical Content purge remains governed future lifecycle work. Ordinary Generic Content runtime has no purge authority.

`content.content_versions` UPDATE/DELETE remain additionally blocked by immutability triggers. Triggers are not a substitute for withholding those privileges from runtime.

Tenant context is transaction-local (`SET LOCAL` / `set_config(..., is_local := true)` for `aieos.tenant_id`). Session-persistent `SET` is not an application security mechanism.

## Out of scope

Cluster-level production role provisioning, secrets, deployment of these identities, and physical purge authorization.

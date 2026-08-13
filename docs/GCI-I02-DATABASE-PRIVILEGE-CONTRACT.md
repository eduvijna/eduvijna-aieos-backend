---
id: GCI-I02-DATABASE-PRIVILEGE-CONTRACT
title: Generic Content database privilege contract
status: draft
version: 0.1.0
---

# Generic Content database privilege contract (GCI-I02)

This document describes the required production privilege separation. It does **not** provision cloud or production identities.

ADR-AIEOS-024: runtime identity ≠ migration identity ≠ schema owner ≠ backup/restore authority.

## Identities

| Identity | Purpose | Must not have |
|----------|---------|----------------|
| Schema owner | Owns `content` schema objects | Application runtime login, if avoidable |
| Migrator | Alembic upgrade/downgrade only | Product runtime DML path; `BYPASSRLS` as a substitute for application tenancy |
| Runtime | Ordinary Generic Content DML | Superuser, `BYPASSRLS`, schema ownership |
| Backup/restore | Backup and restore | Ordinary product DML |

A runtime identity must **not** require `BYPASSRLS`, superuser, or schema ownership.

## Runtime grants (logical)

- `CONNECT` on the application database
- `USAGE` on schema `content`
- `SELECT`, `INSERT`, `UPDATE`, `DELETE` on `content.contents`
- `SELECT`, `INSERT` on `content.content_versions` (UPDATE/DELETE remain blocked by immutability triggers)
- `EXECUTE` on `content.current_tenant_id()`

Tenant context is transaction-local (`SET LOCAL` / `set_config(..., is_local := true)` for `aieos.tenant_id`). Session-persistent `SET` is not an application security mechanism.

## Out of scope for GCI-I02

Cluster-level production role provisioning, secrets, and deployment of these identities.

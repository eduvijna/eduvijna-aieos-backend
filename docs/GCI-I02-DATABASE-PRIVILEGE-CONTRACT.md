---
id: GCI-I02-DATABASE-PRIVILEGE-CONTRACT
title: Generic Content and API infrastructure database privilege contract
status: draft
version: 0.7.0
---

# Database privilege contract (GCI-I02R2 / GCI-I05R1 / GCI-I06 / GCI-I07 / GCI-I08)

This document describes the required production privilege separation. It does **not** provision cloud or production identities.

ADR-AIEOS-024: runtime identity ≠ migration identity ≠ schema owner ≠ backup/restore authority ≠ workflow dispatcher ≠ event dispatcher.

Production role creation remains infrastructure/deployment work, not application migration work. Alembic must not `CREATE ROLE` or store production credentials.

## Identities

| Identity | Purpose | Login | Must not have |
|----------|---------|-------|----------------|
| Schema owner (`aieos_content_owner` conceptually) | Owns `content`, `api`, `workflow`, and `integration` schema objects | **NOLOGIN** | Application runtime login |
| Migrator | Alembic upgrade/downgrade only; may `SET ROLE` to schema owner for DDL | LOGIN | Product runtime DML path; `BYPASSRLS` as a substitute for application tenancy; schema ownership of application schemas |
| Runtime | Ordinary Generic Content DML, synchronous API idempotency DML, workflow-intent INSERT/SELECT, and outbox INSERT | LOGIN | Superuser, `BYPASSRLS`, schema ownership, `DELETE` on Content tables, `UPDATE`/`DELETE` on outbox, dispatcher capabilities |
| Workflow dispatcher (`aieos_workflow_dispatcher` conceptually) | Claim/retry/deliver/quarantine workflow intents only | LOGIN | Superuser, `BYPASSRLS`, schema ownership, Content/ReviewDecision mutation, workflow-intent INSERT/DELETE |
| Event dispatcher (`aieos_event_dispatcher` conceptually) | Claim/retry/publish/quarantine outbox delivery metadata only | LOGIN | Superuser, `BYPASSRLS`, schema ownership, outbox INSERT/DELETE, Content/ReviewDecision/workflow-intent mutation |
| Backup/restore | Backup and restore | deployment-defined | Ordinary product DML |

A runtime or dispatcher identity must **not** require `BYPASSRLS`, superuser, or schema ownership.

## Alembic role assumption

Alembic connects as the **migrator** identity and executes infrastructure DDL only after an explicit:

```text
AIEOS_SCHEMA_OWNER_ROLE=<schema-owner-role>
```

The migration environment issues `SET LOCAL ROLE` to that role. Online migrations execute it on the migrator connection. Offline SQL generation emits the same `SET LOCAL ROLE` inside the migration transaction before DDL. The named schema-owner role must already exist; Alembic does not `CREATE ROLE` for production.

After upgrade:

- `content` / `api` / `workflow` / `integration` schema owner == schema-owner role
- `integration.outbox_messages` owner == schema-owner role
- `integration.current_tenant_id()` owner == schema-owner role
- session migration identity (`session_user`) remains the migrator
- runtime identity ≠ workflow dispatcher ≠ event dispatcher ≠ schema owner ≠ migrator

## Runtime grants (logical)

### `content` / `api` / `workflow`

Unchanged from GCI-I07 for Content, API idempotency, and workflow intent INSERT/SELECT boundaries.

### `integration` (GCI-I08 transactional outbox)

`integration.outbox_messages` is event-publication infrastructure, not Content SoR.

Runtime:

- `USAGE` on schema `integration`
- `integration.outbox_messages`: `INSERT` only — **not** `SELECT` unless strictly required, **not** `UPDATE`, **not** `DELETE`
- `EXECUTE` on `integration.current_tenant_id()`

Event dispatcher:

- `USAGE` on schema `integration`
- `SELECT` on `integration.outbox_messages`
- column-limited `UPDATE` only for delivery metadata:
  `status`, `attempt_count`, `available_at`, `claimed_by`, `claimed_until`, `published_at`, `broker_stream`, `broker_sequence`, `last_error_code`
- **not** `INSERT`, **not** `DELETE`
- `EXECUTE` on `integration.current_tenant_id()`
- **no** Content / ReviewDecision / workflow-intent mutation grants
- **NOSUPERUSER**, **NOBYPASSRLS**, not schema owner

`integration.outbox_messages` has ENABLE RLS and FORCE RLS. Tenant isolation uses transaction-local `aieos.tenant_id` via `integration.current_tenant_id()`. Missing tenant context must fail closed.

Immutable event facts (`event_id`, tenant/aggregate identity, event type/subject, envelope, `created_at`) are protected by a database immutability guard. Dispatcher updates may change only delivery metadata.

Dispatcher APIs are tenant-scoped (`dispatch_once(tenant_id)`). GCI-I08 does not authorize BYPASSRLS for global tenant scanning. No production dispatcher daemon or production NATS credentials are authorized by this contract.

## Tenant context

Tenant context is transaction-local (`SET LOCAL` / `set_config(..., is_local := true)` for `aieos.tenant_id`) for `content`, `api`, `workflow`, and `integration` policies.

## Out of scope

Cluster-level production role provisioning, secrets, deployment of these identities, production NATS topology, and physical purge authorization.

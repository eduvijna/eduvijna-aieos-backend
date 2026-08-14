---
id: GCI-I02-DATABASE-PRIVILEGE-CONTRACT
title: Generic Content and API infrastructure database privilege contract
status: draft
version: 1.0.0
---

# Database privilege contract (GCI-I02R2 / GCI-I05R1 / GCI-I06 / GCI-I07 / GCI-I08 / GCI-I09 / GCI-I10 / GCI-I11 / GCI-I13)

This document describes the required production privilege separation. It does **not** provision cloud or production identities.

ADR-AIEOS-024: runtime identity ≠ migration identity ≠ schema owner ≠ backup/restore authority ≠ workflow dispatcher ≠ event dispatcher ≠ content migration workload.

Production role creation remains infrastructure/deployment work, not application migration work. Alembic must not `CREATE ROLE` or store production credentials.

## Identities

| Identity | Purpose | Login | Must not have |
|----------|---------|-------|----------------|
| Schema owner (`aieos_content_owner` conceptually) | Owns `content`, `api`, `workflow`, and `integration` schema objects | **NOLOGIN** | Application runtime login |
| Migrator | Alembic upgrade/downgrade only; may `SET ROLE` to schema owner for DDL | LOGIN | Product runtime DML path; `BYPASSRLS` as a substitute for application tenancy; schema ownership of application schemas |
| Runtime | Ordinary Generic Content DML, synchronous API idempotency DML, workflow-intent INSERT/SELECT, Publication INSERT/SELECT, VersionAssetRef INSERT/SELECT, and outbox INSERT | LOGIN | Superuser, `BYPASSRLS`, schema ownership, `DELETE` on Content tables, `UPDATE`/`DELETE` on publications, version_asset_refs, or outbox, dispatcher capabilities, write on `migration_import_records` |
| Content migration runtime (`aieos_content_migration_runtime` conceptually) | Controlled ImportMigratedContentService DML only | LOGIN | Superuser, `BYPASSRLS`, schema ownership, ReviewDecision/Publication INSERT, DELETE on migration records, UPDATE/DELETE on versions |
| Workflow dispatcher (`aieos_workflow_dispatcher` conceptually) | Claim/retry/deliver/quarantine workflow intents only | LOGIN | Superuser, `BYPASSRLS`, schema ownership, Content/ReviewDecision/Publication/VersionAssetRef mutation, workflow-intent INSERT/DELETE |
| Event dispatcher (`aieos_event_dispatcher` conceptually) | Claim/retry/publish/quarantine outbox delivery metadata only | LOGIN | Superuser, `BYPASSRLS`, schema ownership, outbox INSERT/DELETE, Content/ReviewDecision/Publication/VersionAssetRef/workflow-intent mutation |
| Backup/restore | Backup and restore | deployment-defined | Ordinary product DML |

A runtime, migration workload, or dispatcher identity must **not** require `BYPASSRLS`, superuser, or schema ownership.

## Alembic role assumption

Alembic connects as the **migrator** identity and executes infrastructure DDL only after an explicit:

```text
AIEOS_SCHEMA_OWNER_ROLE=<schema-owner-role>
```

The migration environment issues `SET LOCAL ROLE` to that role. Online migrations execute it on the migrator connection. Offline SQL generation emits the same `SET LOCAL ROLE` inside the migration transaction before DDL. The named schema-owner role must already exist; Alembic does not `CREATE ROLE` for production.

After upgrade:

- `content` / `api` / `workflow` / `integration` schema owner == schema-owner role
- `content.publications` owner == schema-owner role
- `content.version_asset_refs` owner == schema-owner role
- `integration.outbox_messages` owner == schema-owner role
- `integration.current_tenant_id()` owner == schema-owner role
- session migration identity (`session_user`) remains the migrator
- runtime identity ≠ workflow dispatcher ≠ event dispatcher ≠ schema owner ≠ migrator

## Runtime grants (logical)

### `content` / `api` / `workflow`

Unchanged from GCI-I07 for Content heads/versions, ReviewDecision INSERT/SELECT, API idempotency, and workflow intent INSERT/SELECT boundaries.

### `content.publications` (GCI-I09)

`content.publications` is immutable Publication history (not a stewardship state).

Runtime:

- `SELECT`, `INSERT` on `content.publications`
- **not** `UPDATE`, **not** `DELETE`

`content.publications` has ENABLE RLS and FORCE RLS. Tenant isolation uses transaction-local `aieos.tenant_id` via `content.current_tenant_id()`. Missing tenant context must fail closed.

Privileged UPDATE/DELETE are blocked by an immutability trigger even for the schema owner path used in privileged tests.

### `content.version_asset_refs` (GCI-I10)

`content.version_asset_refs` is immutable ContentVersion → Asset ResourceRef association history. It is not Asset storage and not authorization truth.

Runtime:

- `SELECT`, `INSERT` on `content.version_asset_refs`
- **not** `UPDATE`, **not** `DELETE`

`content.version_asset_refs` has ENABLE RLS and FORCE RLS. Tenant isolation uses transaction-local `aieos.tenant_id` via `content.current_tenant_id()`. Missing tenant context must fail closed.

Privileged UPDATE/DELETE are blocked by an immutability trigger even for the schema owner path used in privileged tests.

### `content.ai_generation_provenance_v1_is_valid` (GCI-I11)

Schema-owned immutable SQL function used only by `ck_content_versions_ai_provenance_v1` for `origin = AI` defense-in-depth. Companion helper: `content.resource_ref_json_is_valid(jsonb)`.

Runtime DML does **not** require `EXECUTE` on these functions: CHECK evaluation runs with table-owner privileges. No additional runtime GRANT is required beyond existing `content.content_versions` INSERT/SELECT.

No new Content table is introduced by GCI-I11.

### `content.migration_import_records` + IMPORT provenance (GCI-I13)

`content.migration_import_records` is migration infrastructure evidence, not Content aggregate state. Companion validator: `content.migration_import_provenance_v1_is_valid(jsonb)` for `origin = IMPORT`.

Content migration runtime (`aieos_content_migration_runtime` conceptually):

- `SELECT`, `INSERT`, `UPDATE` on `content.contents` (guarded head updates)
- `SELECT`, `INSERT` on `content.content_versions` — **not** `UPDATE`, **not** `DELETE`
- `SELECT`, `INSERT` on `content.version_asset_refs` — **not** `UPDATE`, **not** `DELETE`
- `SELECT`, `INSERT`, `UPDATE` on `content.migration_import_records` — **not** `DELETE`
- `INSERT` on `integration.outbox_messages`
- `EXECUTE` on `content.current_tenant_id()` / `integration.current_tenant_id()`
- **not** ReviewDecision INSERT, **not** Publication INSERT, **not** workflow/idempotency mutation

Ordinary API runtime:

- may `SELECT` migration records if needed for diagnostics
- **must not** `INSERT` / `UPDATE` / `DELETE` `content.migration_import_records`

`content.migration_import_records` has ENABLE RLS and FORCE RLS. Tenant isolation uses transaction-local `aieos.tenant_id` via `content.current_tenant_id()`. Missing tenant context must fail closed. Source-evidence fields are immutable after insert; `IMPORTED` is terminal.

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
- **no** Content / ReviewDecision / Publication / VersionAssetRef / workflow-intent mutation grants
- **NOSUPERUSER**, **NOBYPASSRLS**, not schema owner

`integration.outbox_messages` has ENABLE RLS and FORCE RLS. Tenant isolation uses transaction-local `aieos.tenant_id` via `integration.current_tenant_id()`. Missing tenant context must fail closed.

Immutable event facts (`event_id`, tenant/aggregate identity, event type/subject, envelope, `created_at`) are protected by a database immutability guard. Dispatcher updates may change only delivery metadata.

Dispatcher APIs are tenant-scoped (`dispatch_once(tenant_id)`). GCI-I08 does not authorize BYPASSRLS for global tenant scanning. No production dispatcher daemon or production NATS credentials are authorized by this contract.

## Tenant context

Tenant context is transaction-local (`SET LOCAL` / `set_config(..., is_local := true)` for `aieos.tenant_id`) for `content`, `api`, `workflow`, and `integration` policies.

## Out of scope

Cluster-level production role provisioning, secrets, deployment of these identities, production NATS topology, and physical purge authorization.

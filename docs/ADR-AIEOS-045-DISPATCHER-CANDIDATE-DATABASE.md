---
id: ADR-AIEOS-045-DISPATCHER-CANDIDATE-DATABASE
title: Dispatcher tenant-candidate database authority (backend)
status: draft
version: 1.0.0
---

# ADR-AIEOS-045 — Dispatcher candidate database authority (backend)

Architecture authority: **ADR-AIEOS-045** (Frozen / Approved).

This document describes the Backend-owned PostgreSQL migration surface for tenant-candidate discovery. It does **not** authorize production execution, dispatcher daemons, or Infrastructure role provisioning beyond the documented handshake.

## Scope

| Item | Status |
|------|--------|
| Alembic revision `adra045001` (`down_revision` `pedi10b6001`) | Source-defined |
| Role-scoped RLS replacement on outbox / workflow intent queues | Source-defined |
| Candidate-reader column grants + SECURITY DEFINER candidate functions | Source-defined |
| EVENT candidate indexes | Source-defined |
| Production migration execution | **NOT AUTHORIZED** |
| Dispatcher daemon implement / deploy | **NOT AUTHORIZED** |
| Scheduled / reconciliation runtime | **OPEN** |

## Migration

- Revision: `adra045001`
- File: `migrations/versions/adra045001_dispatcher_candidate_authority.py`
- Preceding head: `pedi10b6001`
- Alembic must **not** `CREATE ROLE` / `ALTER ROLE` / grant candidate-reader membership to the migrator

## Role inputs (fail closed)

| Environment variable | Purpose |
|----------------------|---------|
| `AIEOS_SCHEMA_OWNER_ROLE` | Content schema owner |
| `AIEOS_SECURITY_SCHEMA_OWNER_ROLE` | Security schema owner |
| `AIEOS_RUNTIME_ROLE` | Ordinary API runtime |
| `AIEOS_CONTENT_MIGRATION_RUNTIME_ROLE` | Content migration runtime |
| `AIEOS_EVENT_DISPATCHER_ROLE` | Event dispatcher LOGIN |
| `AIEOS_WORKFLOW_DISPATCHER_ROLE` | Workflow dispatcher LOGIN |
| `AIEOS_EVENT_CANDIDATE_READER_ROLE` | Event candidate-reader (NOLOGIN) |
| `AIEOS_WORKFLOW_CANDIDATE_READER_ROLE` | Workflow candidate-reader (NOLOGIN) |

Missing, invalid, or aliased role inputs fail closed before universal policy drop.

## RLS model

Universal `FOR ALL` tenant policies on `integration.outbox_messages` and workflow intent tables are replaced with role-target policies:

- owner / runtime / dispatcher policies retain `current_tenant_id()` tenancy
- candidate-reader policies are **SELECT-only** on `status IN ('PENDING', 'CLAIMED')` and do **not** use `current_tenant_id()` (controlled cross-tenant scheduling visibility)
- FORCE RLS remains enabled; migration never disables FORCE RLS

## Function contracts

| Function | Owner | EXECUTE |
|----------|-------|---------|
| `integration.list_outbox_dispatch_candidates(integer, timestamptz)` | event candidate-reader | event dispatcher only |
| `workflow.list_start_intent_candidates(integer, timestamptz)` | workflow candidate-reader | workflow dispatcher only |
| `workflow.list_command_intent_candidates(integer, timestamptz)` | workflow candidate-reader | workflow dispatcher only |

Shared constraints:

- `SECURITY DEFINER`, `STABLE`, pinned `search_path = pg_catalog, pg_temp`
- `PUBLIC` EXECUTE revoked
- returns only `(tenant_id uuid, eligible_at timestamptz)`
- no payload / envelope visibility; cross-tenant payload visibility = **NONE**
- no dynamic SQL; no mutation; no tenant-context mutation; no RLS disable

Function ownership uses temporary `SET LOCAL ROLE` to the candidate-reader after Infrastructure JIT `SET` membership (`ADMIN FALSE`, `INHERIT FALSE`, `SET TRUE`). Alembic never grants that membership.

## Indexes

| Index | Table |
|-------|-------|
| `ix_outbox_messages_candidate_pending` | `integration.outbox_messages` |
| `ix_outbox_messages_candidate_claimed` | `integration.outbox_messages` |

No new workflow candidate indexes in this revision.

## Infrastructure bootstrap dependency

Backend CI / disposable acceptance that exercises Infrastructure scripts must pin:

```text
eduvijna/eduvijna-aieos-infrastructure@1249634403cacd9caec4ba48b72821e629b222f5
```

Scripts (Infrastructure-owned):

- `scripts/postgresql/bootstrap-candidate-readers.sh`
- `scripts/postgresql/grant-candidate-migration-access.sh`
- `scripts/postgresql/revoke-candidate-migration-access.sh`
- `scripts/postgresql/verify-candidate-readers.sh`
- `scripts/postgresql/cleanup-candidate-migration-access.sh`

## Authorization status

| Concern | Authorization |
|---------|---------------|
| Production Alembic execution of `adra045001` | **NOT AUTHORIZED** |
| Production candidate-reader provisioning | Infrastructure source-defined / **NOT RELEASED** |
| Dispatcher daemon | **NOT AUTHORIZED** |
| Scheduled / reconciliation runtime | **OPEN** |

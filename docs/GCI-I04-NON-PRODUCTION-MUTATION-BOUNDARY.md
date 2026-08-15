---
id: SAI-I04-NON-PRODUCTION-MUTATION-BOUNDARY
title: API, AI, and migration committed-mutation audit integrated; production still blocked
status: draft
version: 1.5.0
---

# SAI-I04 non-production mutation boundary

Idempotency-Key is now required for:

- `POST /api/v1/contents`
- `POST /api/v1/contents/{content_id}/versions`
- `POST /api/v1/contents/{content_id}/versions/{version_id}/actions/submit-for-review`
- `POST /api/v1/contents/{content_id}/versions/{version_id}/actions/approve`
- `POST /api/v1/contents/{content_id}/versions/{version_id}/actions/request-changes`
- `POST /api/v1/contents/{content_id}/versions/{version_id}/actions/reject`
- `POST /api/v1/contents/{content_id}/actions/publish`

GCI-I12–I14 remain as previously documented (Review Queue reads, migration foundation, adversarial validation).

## ADR-AIEOS-028 / SAI-I01–I04

ADR-AIEOS-028 (Security Audit & Mutation Accountability) is frozen.

SAI-I01 framework-neutral security mutation-audit contracts exist under
`src/aieos/platform/security/audit/`.

SAI-I02 adds the PostgreSQL security audit ledger (`security.audit_records`,
Alembic `saii020001`).

SAI-I03 wires **API-origin** Generic Content mutations to insert one
`SecurityMutationAuditRecord` in the same Content UoW transaction.

SAI-I04 wires the remaining committed-mutation audit gaps:

- `content.ai.materialize` via `MaterializeAIGeneratedContentVersionService`
  (`SecurityAuditExecutionChannel.AI_MATERIALIZATION`)
- `content.migration.import` via `ImportMigratedContentService`
  (`SecurityAuditExecutionChannel.MIGRATION`)

Both reuse `ContentUnitOfWork.audit` / `insert_required_content_audit` —
no second audit repository, connection, or transaction.

## Workflow-origin status (N/A)

`ContentReviewWorkflowV1` is process truth only (run / signal / query).
It does **not** execute a Content-mutating Temporal Activity.

Therefore:

- `SecurityAuditExecutionChannel.WORKFLOW_ACTIVITY` remains a frozen enum value
- no current Content implementation inserts WORKFLOW_ACTIVITY audit rows
- receiving `review_decision_recorded` is observation only — the authoritative
  review audit was already written by the API path (SAI-I03)

### Future workflow rule (documentation only — not implemented)

If a future Temporal Activity invokes a protected Content business command, it must:

1. revalidate current authority
2. invoke the normal application command
3. supply explicit `WORKFLOW_ACTIVITY` provenance
4. write business + outbox + audit atomically

Do not invent such an Activity in SAI-I04.

## Authority separation

- `AIGenerationProvenanceV1` = AI generation provenance (ContentVersion)
- `SecurityMutationAuditRecord` = committed-mutation security evidence
- `content.migration_import_records` FAILED/IMPORTED = migration execution evidence  
  (FAILED evidence is **not** a successful security committed-mutation audit)

## Still required before production declaration

- SAI-I05 final adversarial audit/security gate
- production role/credential provisioning
- production runtime/environment validation
- any other frozen deployment gates

Therefore:

- production mutation remains **NOT AUTHORIZED**
- production migration remains **NOT AUTHORIZED**
  (no legacy connectors, production migration runner, cutover, or real
  legacy/AIEOS write coexistence)

SAI-I04 does **not**:

- add Alembic `saii030001` / `saii040001` (head remains `saii020001`)
- expose audit HTTP / OpenAPI
- implement failed/denied-attempt audit, SIEM export, or crypto sealing
- change MutationEventContext or TrustedSecurityContext
- change Temporal workflow definitions, Activities, or JetStream contracts
- add AI/migration HTTP product entrypoints

Migration head:

`gcii020001 → … → gcii110001 → gcii130001 → saii020001`

## Production role provisioning boundary

Ephemeral test fixtures may create/grant `aieos_security_owner`, migrator
`SET ROLE` membership, and runtime/migration-runtime `USAGE` + `INSERT` on
`security.audit_records`. That does **not** complete production provisioning.

Before eventual production deployment, operators must provision:

- a distinct security schema owner (`AIEOS_SECURITY_SCHEMA_OWNER_ROLE`)
- migrator membership / `SET ROLE` capability into that owner
- runtime and content-migration-runtime INSERT-only privileges on the ledger
  (no SELECT/UPDATE/DELETE; no schema ownership; no `BYPASSRLS`)

No production deployment or production database mutation entrypoint is authorized by this slice.

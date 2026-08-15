---
id: SAI-I05-NON-PRODUCTION-MUTATION-BOUNDARY
title: Security-audit implementation baseline complete; production still blocked
status: draft
version: 1.6.0
---

# SAI-I05 non-production mutation boundary

Idempotency-Key is now required for:

- `POST /api/v1/contents`
- `POST /api/v1/contents/{content_id}/versions`
- `POST /api/v1/contents/{content_id}/versions/{version_id}/actions/submit-for-review`
- `POST /api/v1/contents/{content_id}/versions/{version_id}/actions/approve`
- `POST /api/v1/contents/{content_id}/versions/{version_id}/actions/request-changes`
- `POST /api/v1/contents/{content_id}/versions/{version_id}/actions/reject`
- `POST /api/v1/contents/{content_id}/actions/publish`

GCI-I12–I14 remain as previously documented (Review Queue reads, migration foundation, adversarial validation).

## AIEOS Security Audit Implementation Baseline (SAI-I01–SAI-I05)

ADR-AIEOS-028 (Security Audit & Mutation Accountability) is frozen.

**AIEOS Security Audit Implementation Baseline SAI-I01–SAI-I05 = IMPLEMENTATION-BASELINE COMPLETE**

Classification: **NON-PRODUCTION / DEPLOYMENT NOT YET AUTHORIZED**.

| Slice | Scope |
|-------|--------|
| SAI-I01 | Framework-neutral security mutation-audit contracts |
| SAI-I02 / I02R1 | PostgreSQL `security.audit_records` ledger, RLS, ResourceRef defense |
| SAI-I03 | API-origin Generic Content committed-mutation audit (same txn) |
| SAI-I04 | AI materialize + controlled migration import audit (same txn) |
| SAI-I05 | Final adversarial / transaction / tenancy / implementation-readiness gate |

All **currently implemented** Generic Content protected committed mutations write required
security audit evidence in the same authoritative PostgreSQL transaction as business
mutation + required outbox (+ workflow/idempotency intent where applicable):

- `content.create`
- `content.version.create` (HTTP human append)
- `content.review.submit` / `approve` / `request_changes` / `reject`
- `content.publish`
- `content.ai.materialize`
- `content.migration.import`

Workflow-origin Content mutation remains **NONE / N/A** (`ContentReviewWorkflowV1` is
process truth only; `WORKFLOW_ACTIVITY` unused).

### Future workflow rule (documentation only — not implemented)

If a future Temporal Activity invokes a protected Content business command, it must:

1. revalidate current authority
2. invoke the normal application command
3. supply explicit `WORKFLOW_ACTIVITY` provenance
4. write business + outbox + audit atomically

Do not invent such an Activity in SAI-I05.

Archive and physical purge remain **NOT IMPLEMENTED**.

## Authority separation

- `AIGenerationProvenanceV1` = AI generation provenance (ContentVersion)
- `SecurityMutationAuditRecord` = committed-mutation security evidence
- `content.migration_import_records` FAILED/IMPORTED = migration execution evidence  
  (FAILED evidence is **not** a successful security committed-mutation audit)
- ReviewDecision / Publication / Content remain business truth; audit is not queried
  to authorize or decide state

## Production provisioning boundary

Ephemeral test fixtures may create/grant `aieos_security_owner`, migrator
`SET ROLE` membership, and runtime/migration-runtime `USAGE` + `INSERT` on
`security.audit_records`. That does **not** complete production provisioning and does
**not** verify real production credentials, secret storage, database users, network
policy, TLS, backup controls, or operational access.

SAI-I05 is a **repository implementation-readiness gate**. It does **not** validate
the actual production environment. A complete PASS does **not** authorize production
mutation, migration, or deployment.

Before eventual production deployment, operators must provision:

- a distinct security schema owner (`AIEOS_SECURITY_SCHEMA_OWNER_ROLE`)
- migrator membership / `SET ROLE` capability into that owner
- runtime and content-migration-runtime INSERT-only privileges on the ledger
  (no SELECT/UPDATE/DELETE; no schema ownership; no `BYPASSRLS`)

## Explicit authorization status

- production mutation remains **NOT AUTHORIZED**
- production migration remains **NOT AUTHORIZED**
- production deployment remains **NOT AUTHORIZED**

Do **not** treat this baseline as production-approved, deployment-cleared, or
mutation-authorized.

## What SAI-I05 does not add

- no production `src/` or Alembic migration changes (`saii030001`/`saii040001`/`saii050001` absent; head remains `saii020001`)
- no audit HTTP / OpenAPI surface
- no failed/denied-attempt audit, SIEM/export, or crypto sealing
- no new Content mutation, workflow Activity, archive, or purge
- no frontend / feature-flag change

Migration head:

`gcii020001 → … → gcii110001 → gcii130001 → saii020001`

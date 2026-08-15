---
id: SAI-I03-NON-PRODUCTION-MUTATION-BOUNDARY
title: API-origin Content mutations have transactional audit; production still blocked
status: draft
version: 1.4.0
---

# SAI-I03 non-production mutation boundary

Idempotency-Key is now required for:

- `POST /api/v1/contents`
- `POST /api/v1/contents/{content_id}/versions`
- `POST /api/v1/contents/{content_id}/versions/{version_id}/actions/submit-for-review`
- `POST /api/v1/contents/{content_id}/versions/{version_id}/actions/approve`
- `POST /api/v1/contents/{content_id}/versions/{version_id}/actions/request-changes`
- `POST /api/v1/contents/{content_id}/versions/{version_id}/actions/reject`
- `POST /api/v1/contents/{content_id}/actions/publish`

GCI-I12–I14 remain as previously documented (Review Queue reads, migration foundation, adversarial validation).

## ADR-AIEOS-028 / SAI-I01 / SAI-I02 / SAI-I03

ADR-AIEOS-028 (Security Audit & Mutation Accountability) is frozen.

SAI-I01 framework-neutral security mutation-audit contracts exist under
`src/aieos/platform/security/audit/`.

SAI-I02 adds the PostgreSQL security audit ledger (`security.audit_records`,
Alembic `saii020001`).

SAI-I03 wires **API-origin** Generic Content mutations to insert one
`SecurityMutationAuditRecord` in the **same** Content UoW / PostgreSQL
transaction as business state, outbox intent, workflow intent (where
applicable), and idempotency outcome:

- `content.create`
- `content.version.create` (human HTTP append only)
- `content.review.submit`
- `content.review.approve`
- `content.review.request_changes`
- `content.review.reject`
- `content.publish`

## Still missing (SAI-I04+)

- `content.ai.materialize` audit integration
- `content.migration.import` audit integration
- any required workflow-origin protected mutation audit (`WORKFLOW_ACTIVITY`)
- final adversarial production gate (SAI-I05)

Therefore:

- production mutation remains **NOT AUTHORIZED**
- production migration remains **NOT AUTHORIZED**
- Content mutations and controlled migration import remain **NON-PRODUCTION**

SAI-I03 does **not**:

- add Alembic `saii030001` (head remains `saii020001`)
- expose audit HTTP / OpenAPI
- implement failed/denied-attempt audit, SIEM export, or crypto sealing
- change MutationEventContext or TrustedSecurityContext
- change Temporal workflow definitions or JetStream event contracts

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

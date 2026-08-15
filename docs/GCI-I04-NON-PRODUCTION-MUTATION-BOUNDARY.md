---
id: SAI-I02-NON-PRODUCTION-MUTATION-BOUNDARY
title: Generic Content mutations remain non-production; security audit ledger exists without Content integration
status: draft
version: 1.3.0
---

# SAI-I02 non-production mutation boundary

Idempotency-Key is now required for:

- `POST /api/v1/contents`
- `POST /api/v1/contents/{content_id}/versions`
- `POST /api/v1/contents/{content_id}/versions/{version_id}/actions/submit-for-review`
- `POST /api/v1/contents/{content_id}/versions/{version_id}/actions/approve`
- `POST /api/v1/contents/{content_id}/versions/{version_id}/actions/request-changes`
- `POST /api/v1/contents/{content_id}/versions/{version_id}/actions/reject`
- `POST /api/v1/contents/{content_id}/actions/publish`

GCI-I12–I14 remain as previously documented (Review Queue reads, migration foundation, adversarial validation).

## ADR-AIEOS-028 / SAI-I01 / SAI-I02

ADR-AIEOS-028 (Security Audit & Mutation Accountability) is frozen.

SAI-I01 framework-neutral security mutation-audit contracts exist under
`src/aieos/platform/security/audit/`.

SAI-I02 adds the PostgreSQL security audit ledger:

- schema `security` owned by configured `AIEOS_SECURITY_SCHEMA_OWNER_ROLE`
- table `security.audit_records` (append-only, FORCE RLS, INSERT-only policy)
- SQLAlchemy mapping + insert-only `SqlAlchemySecurityMutationAuditRepository`
- Alembic revision `saii020001` (revises `gcii130001`; there is no `saii010001`)

SAI-I02 does **not**:

- wire Content mutations (create/append/review/publish/AI/migration) to write audit rows
- expose audit HTTP / OpenAPI
- implement failed/denied-attempt audit, SIEM export, or crypto sealing
- add a security audit reader role or SELECT policy

Therefore:

- protected Content transactions can still currently commit **without** writing
  `security.audit_records`
- Content mutations and controlled migration import remain **NON-PRODUCTION**
- production mutation and production migration remain **NOT AUTHORIZED**

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

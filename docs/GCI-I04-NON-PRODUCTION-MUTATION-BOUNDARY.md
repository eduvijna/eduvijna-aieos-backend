---
id: SAI-I01-NON-PRODUCTION-MUTATION-BOUNDARY
title: Generic Content mutations remain non-production; security audit contracts exist without persistence
status: draft
version: 1.2.0
---

# SAI-I01 non-production mutation boundary

Idempotency-Key is now required for:

- `POST /api/v1/contents`
- `POST /api/v1/contents/{content_id}/versions`
- `POST /api/v1/contents/{content_id}/versions/{version_id}/actions/submit-for-review`
- `POST /api/v1/contents/{content_id}/versions/{version_id}/actions/approve`
- `POST /api/v1/contents/{content_id}/versions/{version_id}/actions/request-changes`
- `POST /api/v1/contents/{content_id}/versions/{version_id}/actions/reject`
- `POST /api/v1/contents/{content_id}/actions/publish`

GCI-I12–I14 remain as previously documented (Review Queue reads, migration foundation, adversarial validation).

## ADR-AIEOS-028 / SAI-I01

ADR-AIEOS-028 (Security Audit & Mutation Accountability) is frozen.

SAI-I01 adds **framework-neutral** security mutation-audit contracts under
`src/aieos/platform/security/audit/`:

- `AuditRecordId` (UUIDv7)
- typed `SecurityAuditAction` / `SecurityAuditExecutionChannel`
- `SecurityMutationAuditContext` derived from `MutationEventContext`
- immutable `SecurityMutationAuditRecord`
- canonical `build_security_mutation_audit_record(...)`
- insert-only `SecurityMutationAuditRepository` port

SAI-I01 does **not** create:

- `security.audit_records` or any audit ledger table
- Alembic revision (`saii010001` / `gcii150001` / etc.)
- Content mutation integration (create/append/review/publish/AI/migration)
- public audit HTTP / OpenAPI surface
- failed/denied-attempt audit, SIEM export, or crypto sealing

Therefore:

- required transactional security-audit **intent persistence** remains absent
- Content mutations and controlled migration import remain **NON-PRODUCTION**
- production mutation and production migration remain **NOT AUTHORIZED**

Migration head remains:

`gcii020001 → … → gcii110001 → gcii130001`

No production deployment or production database mutation entrypoint is authorized by this slice.

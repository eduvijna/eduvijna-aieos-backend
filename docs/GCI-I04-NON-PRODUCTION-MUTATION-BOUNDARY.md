---
id: GCI-I13-NON-PRODUCTION-MUTATION-BOUNDARY
title: Generic Content HTTP mutations and controlled migration remain non-production
status: draft
version: 1.0.0
---

# GCI-I13 non-production mutation boundary

Idempotency-Key is now required for:

- `POST /api/v1/contents`
- `POST /api/v1/contents/{content_id}/versions`
- `POST /api/v1/contents/{content_id}/versions/{version_id}/actions/submit-for-review`
- `POST /api/v1/contents/{content_id}/versions/{version_id}/actions/approve`
- `POST /api/v1/contents/{content_id}/versions/{version_id}/actions/request-changes`
- `POST /api/v1/contents/{content_id}/versions/{version_id}/actions/reject`
- `POST /api/v1/contents/{content_id}/actions/publish`

GCI-I12 adds a durable **read-only** Teacher OS Review Queue projection:

- `GET /api/v1/teacher-os/review-queue`
- `GET /api/v1/teacher-os/review-queue/{content_id}/versions/{version_id}`

derived from authoritative Content `IN_REVIEW` current versions. There is no separate Review Queue table or mutation surface.

GCI-I13 adds a controlled **migration adapter foundation**:

- typed `MigrationImportProvenanceV1` for `origin=IMPORT`
- durable `content.migration_import_records` source→target evidence
- GCI-G12 replay / digest / mapping conflict detection
- internal `ImportMigratedContentService` (no public migration HTTP)

All of the HTTP mutation routes above remain a **development / test mutation foundation**.

They MUST NOT be authorized for production mutation until later slices integrate the required transactional:

- security-audit intent persistence

GCI-I08–I13 provide transactional event-publication intent (including publish), ResourceRef dual validation, typed AI provenance materialization, Teacher OS Review Queue reads, and a controlled migration import foundation, but still lack required security-audit intent for mutations. Therefore Content mutations and controlled migration import remain **NON-PRODUCTION**.

GCI-I13 does **not** create:

- public migration HTTP routes (`/migrate`, `/imports`, `/legacy`)
- production legacy connectors (PostgREST, `edu.content`, legacy APIs)
- review/publication trust import from legacy approval/publish state
- archive HTTP or `content.archived` emission
- audit tables / audit dispatchers
- GCI-I14 adversarial suite structures
- a production NATS topology, credentials, or dispatcher daemon
- authorization to read production legacy data or write production AIEOS Content

Durable outbox event-publication intent exists (including publish and import). Required security-audit intent still does not. Idempotency remains synchronous API retry state, not Content business authority and not a substitute for audit intent. Migration replay identity is source evidence, not `Idempotency-Key`.

No production deployment, production database mutation entrypoint, or real production migration batch is authorized by this slice.

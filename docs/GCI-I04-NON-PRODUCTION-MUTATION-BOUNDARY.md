---
id: GCI-I12-NON-PRODUCTION-MUTATION-BOUNDARY
title: Generic Content HTTP mutations are not production mutations
status: draft
version: 0.9.0
---

# GCI-I12 non-production mutation boundary

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

All of the HTTP mutation routes above remain a **development / test mutation foundation**.

They MUST NOT be authorized for production mutation until later slices integrate the required transactional:

- security-audit intent persistence

GCI-I08–I12 provide transactional event-publication intent (including publish), ResourceRef dual validation, typed AI provenance materialization, and Teacher OS Review Queue reads, but still lack required security-audit intent for mutations. Therefore Content mutations remain **NON-PRODUCTION**.

GCI-I12 does **not** create:

- `review_queue` / assignment / claim / notification tables
- Teacher OS frontend wiring / feature flags
- review-decision mutation aliases under Teacher OS
- archive HTTP or `content.archived` emission
- audit tables / audit dispatchers
- GCI-I13 or later structures (migration adapter, adversarial suite)
- a production NATS topology, credentials, or dispatcher daemon

Durable outbox event-publication intent exists (including publish). Required security-audit intent still does not. Idempotency remains synchronous API retry state, not Content business authority and not a substitute for audit intent.

No production deployment or production database mutation entrypoint is authorized by this slice.

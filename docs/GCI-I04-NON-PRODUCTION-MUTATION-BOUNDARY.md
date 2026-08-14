---
id: GCI-I09-NON-PRODUCTION-MUTATION-BOUNDARY
title: Generic Content HTTP mutations are not production mutations
status: draft
version: 0.6.0
---

# GCI-I09 non-production mutation boundary

Idempotency-Key is now required for:

- `POST /api/v1/contents`
- `POST /api/v1/contents/{content_id}/versions`
- `POST /api/v1/contents/{content_id}/versions/{version_id}/actions/submit-for-review`
- `POST /api/v1/contents/{content_id}/versions/{version_id}/actions/approve`
- `POST /api/v1/contents/{content_id}/versions/{version_id}/actions/request-changes`
- `POST /api/v1/contents/{content_id}/versions/{version_id}/actions/reject`
- `POST /api/v1/contents/{content_id}/actions/publish`

GCI-I09 adds immutable `content.publications` history, sets the Content published pointer on publish, and emits `content.published.v1` via the transactional outbox. Publish requires exact-version APPROVE, current version, If-Match, Idempotency-Key, and publication authorization / asset / governance ports.

All of the HTTP routes above remain a **development / test HTTP mutation foundation**.

They MUST NOT be authorized for production mutation until later slices integrate the required transactional:

- security-audit intent persistence

GCI-I08/I09 provide transactional event-publication intent (including publish), but still lack required security-audit intent. Therefore these mutations remain **NON-PRODUCTION**.

GCI-I09 does **not** create:

- audit tables / audit dispatchers
- consumer inbox / business event consumers
- archive HTTP or `content.archived` emission
- `version_asset_refs` asset persistence
- GET publications APIs
- a production NATS topology, credentials, or dispatcher daemon

Durable outbox event-publication intent exists (including publish). Required security-audit intent still does not. Idempotency remains synchronous API retry state, not Content business authority and not a substitute for audit intent.

No production deployment or production database mutation entrypoint is authorized by this slice.

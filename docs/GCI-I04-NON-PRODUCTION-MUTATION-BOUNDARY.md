---
id: GCI-I08-NON-PRODUCTION-MUTATION-BOUNDARY
title: Generic Content HTTP mutations are not production mutations
status: draft
version: 0.5.0
---

# GCI-I08 non-production mutation boundary

Idempotency-Key is now required for:

- `POST /api/v1/contents`
- `POST /api/v1/contents/{content_id}/versions`
- `POST /api/v1/contents/{content_id}/versions/{version_id}/actions/submit-for-review`
- `POST /api/v1/contents/{content_id}/versions/{version_id}/actions/approve`
- `POST /api/v1/contents/{content_id}/versions/{version_id}/actions/request-changes`
- `POST /api/v1/contents/{content_id}/versions/{version_id}/actions/reject`

GCI-I08 adds durable **transactional event-publication intent** (`integration.outbox_messages`) plus a tenant-scoped outbox dispatcher that publishes stored CloudEvents to NATS JetStream.

All of the HTTP routes above remain a **development / test HTTP mutation foundation**.

They MUST NOT be authorized for production mutation until later slices integrate the required transactional:

- security-audit intent persistence

GCI-I08 provides transactional event-publication intent, but still lacks required security-audit intent. Therefore these mutations remain **NON-PRODUCTION**.

GCI-I08 does **not** create:

- audit tables / audit dispatchers
- consumer inbox / business event consumers
- publication tables or publish/archive HTTP
- `content.published` / `content.archived` event emission
- a production NATS topology, credentials, or dispatcher daemon

Durable outbox event-publication intent exists. Required security-audit intent still does not. Idempotency remains synchronous API retry state, not Content business authority and not a substitute for audit intent.

No production deployment or production database mutation entrypoint is authorized by this slice.

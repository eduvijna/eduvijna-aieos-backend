---
id: GCI-I07-NON-PRODUCTION-MUTATION-BOUNDARY
title: Generic Content HTTP mutations are not production mutations
status: draft
version: 0.4.0
---

# GCI-I07 non-production mutation boundary

Idempotency-Key is now required for:

- `POST /api/v1/contents`
- `POST /api/v1/contents/{content_id}/versions`
- `POST /api/v1/contents/{content_id}/versions/{version_id}/actions/submit-for-review`
- `POST /api/v1/contents/{content_id}/versions/{version_id}/actions/approve`
- `POST /api/v1/contents/{content_id}/versions/{version_id}/actions/request-changes`
- `POST /api/v1/contents/{content_id}/versions/{version_id}/actions/reject`

GCI-I07 adds durable Content review workflow **start** and **command** intents plus Temporal delivery components for those intents.

All of the HTTP routes above remain a **development / test HTTP mutation foundation**.

They MUST NOT be authorized for production mutation until later slices integrate the required transactional:

- event-publication intent / transactional outbox (ADR-AIEOS-024 / 025 / 027)
- security-audit intent persistence

GCI-I07 does **not** create:

- `integration.outbox_messages`
- audit tables
- event contracts
- NATS publishers
- publication tables or publish HTTP
- Review Queue list/read APIs
- a production Temporal worker/dispatcher daemon or credentials

Durable workflow start/command intent exists. Required outbox and security-audit intents still do not. Idempotency remains synchronous API retry state, not Content business authority and not a substitute for outbox/audit intent.

No production deployment or production database mutation entrypoint is authorized by this slice.

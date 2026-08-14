---
id: GCI-I06-NON-PRODUCTION-MUTATION-BOUNDARY
title: Generic Content HTTP mutations are not production mutations
status: draft
version: 0.3.0
---

# GCI-I06 non-production mutation boundary

Idempotency-Key is now required for:

- `POST /api/v1/contents`
- `POST /api/v1/contents/{content_id}/versions`
- `POST /api/v1/contents/{content_id}/versions/{version_id}/actions/submit-for-review`
- `POST /api/v1/contents/{content_id}/versions/{version_id}/actions/approve`
- `POST /api/v1/contents/{content_id}/versions/{version_id}/actions/request-changes`
- `POST /api/v1/contents/{content_id}/versions/{version_id}/actions/reject`

All of these routes remain a **development / test HTTP mutation foundation**.

They MUST NOT be authorized for production mutation until later slices integrate:

- transactional outbox / event-publication intent (ADR-AIEOS-024 / 025 / 027)
- required security-audit intent persistence

GCI-I06 does **not** create:

- `integration.outbox_messages`
- audit tables
- event contracts
- NATS publishers
- Temporal workflows or workflow-start intent
- publication tables or publish HTTP
- Review Queue list/read APIs

Idempotency exists for the mutations above. It is synchronous API retry state, not Content business authority and not a substitute for outbox/audit intent.

No production deployment or production database mutation entrypoint is authorized by this slice.

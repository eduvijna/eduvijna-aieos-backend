---
id: GCI-I05-NON-PRODUCTION-MUTATION-BOUNDARY
title: Generic Content HTTP mutations are not production mutations
status: draft
version: 0.2.0
---

# GCI-I05 non-production mutation boundary

Idempotency-Key is now required for:

- `POST /api/v1/contents`
- `POST /api/v1/contents/{content_id}/versions`

Both routes remain a **development / test HTTP mutation foundation**.

They MUST NOT be authorized for production mutation until later slices integrate:

- transactional outbox / event-publication intent (ADR-AIEOS-024 / 025 / 027)
- required security-audit intent persistence

GCI-I05 does **not** create:

- `integration.outbox_messages`
- audit tables
- event contracts
- NATS publishers
- review/publication/archive HTTP
- AI provenance HTTP

`api.idempotency_records` is synchronous API retry state, not Content business authority and not a substitute for outbox/audit intent.

No production deployment or production database mutation entrypoint is authorized by this slice.

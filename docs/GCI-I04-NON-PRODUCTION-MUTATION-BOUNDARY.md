---
id: GCI-I04-NON-PRODUCTION-MUTATION-BOUNDARY
title: Generic Content HTTP create is not a production mutation
status: draft
version: 0.1.0
---

# GCI-I04 non-production mutation boundary

`POST /api/v1/contents` is a **development / test HTTP foundation** for Generic Content create.

It MUST NOT be authorized for production mutation until later slices integrate:

- transactional outbox / event-publication intent (ADR-AIEOS-024 / 025 / 027)
- required security-audit intent persistence
- Idempotency-Key retry-safe create semantics (ADR-AIEOS-025)

GCI-I04 intentionally does **not** create:

- `integration.outbox_messages`
- audit tables
- event contracts
- NATS publishers
- idempotency records
- ContentVersion HTTP routes
- If-Match enforcement

No production deployment or production database mutation entrypoint is authorized by this slice.

---
id: GCI-I11-NON-PRODUCTION-MUTATION-BOUNDARY
title: Generic Content HTTP mutations are not production mutations
status: draft
version: 0.8.0
---

# GCI-I11 non-production mutation boundary

Idempotency-Key is now required for:

- `POST /api/v1/contents`
- `POST /api/v1/contents/{content_id}/versions`
- `POST /api/v1/contents/{content_id}/versions/{version_id}/actions/submit-for-review`
- `POST /api/v1/contents/{content_id}/versions/{version_id}/actions/approve`
- `POST /api/v1/contents/{content_id}/versions/{version_id}/actions/request-changes`
- `POST /api/v1/contents/{content_id}/versions/{version_id}/actions/reject`
- `POST /api/v1/contents/{content_id}/actions/publish`

GCI-I11 adds an inbound AI-generated ContentVersion materialization port with typed allow-listed `AIGenerationProvenanceV1` persisted on existing `content.content_versions.provenance`, plus DB defense-in-depth for `origin=AI`. There is still no public generation HTTP route and no AI provider integration inside Generic Content.

All of the HTTP routes above and the AI materialization application port remain a **development / test mutation foundation**.

They MUST NOT be authorized for production mutation until later slices integrate the required transactional:

- security-audit intent persistence

GCI-I08–I11 provide transactional event-publication intent (including publish), ResourceRef dual validation, and typed AI provenance materialization, but still lack required security-audit intent. Therefore these mutations remain **NON-PRODUCTION**.

GCI-I11 does **not** create:

- AI provider SDKs / credentials / model endpoints
- generation HTTP routes (`/generate`, `/ai`, …)
- AI session / prompt-manager / model-registry tables
- Temporal generation workflows
- audit tables / audit dispatchers
- consumer inbox / business event consumers
- archive HTTP or `content.archived` emission
- GET version-asset-refs or GET publications APIs
- GCI-I12 or later structures (Teacher OS Review Queue, migration adapter, adversarial suite)
- a production NATS topology, credentials, or dispatcher daemon

Durable outbox event-publication intent exists (including publish). Required security-audit intent still does not. Idempotency remains synchronous API retry state, not Content business authority and not a substitute for audit intent.

No production deployment or production database mutation entrypoint is authorized by this slice.

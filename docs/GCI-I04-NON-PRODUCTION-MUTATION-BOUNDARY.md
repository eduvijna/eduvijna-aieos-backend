---
id: GCI-I10-NON-PRODUCTION-MUTATION-BOUNDARY
title: Generic Content HTTP mutations are not production mutations
status: draft
version: 0.7.0
---

# GCI-I10 non-production mutation boundary

Idempotency-Key is now required for:

- `POST /api/v1/contents`
- `POST /api/v1/contents/{content_id}/versions`
- `POST /api/v1/contents/{content_id}/versions/{version_id}/actions/submit-for-review`
- `POST /api/v1/contents/{content_id}/versions/{version_id}/actions/approve`
- `POST /api/v1/contents/{content_id}/versions/{version_id}/actions/request-changes`
- `POST /api/v1/contents/{content_id}/versions/{version_id}/actions/reject`
- `POST /api/v1/contents/{content_id}/actions/publish`

GCI-I10 adds immutable `content.version_asset_refs` associations on ContentVersion append, dual ResourceRef validation (`AssetReferenceValidationPort` at bind time; `AssetCurrentGovernancePort` at publish-time current use), and retains publish authorization/governance. Append request bodies may include `asset_refs`; there are still no public GET asset-ref routes.

All of the HTTP routes above remain a **development / test HTTP mutation foundation**.

They MUST NOT be authorized for production mutation until later slices integrate the required transactional:

- security-audit intent persistence

GCI-I08/I09/I10 provide transactional event-publication intent (including publish) and ResourceRef dual validation, but still lack required security-audit intent. Therefore these mutations remain **NON-PRODUCTION**.

GCI-I10 does **not** create:

- Asset object storage / S3 / binary asset APIs
- audit tables / audit dispatchers
- consumer inbox / business event consumers
- archive HTTP or `content.archived` emission
- GET version-asset-refs or GET publications APIs
- GCI-I11 or later structures
- a production NATS topology, credentials, or dispatcher daemon

Durable outbox event-publication intent exists (including publish). Required security-audit intent still does not. Idempotency remains synchronous API retry state, not Content business authority and not a substitute for audit intent.

No production deployment or production database mutation entrypoint is authorized by this slice.

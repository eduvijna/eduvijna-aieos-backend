# Changelog

All notable changes to the EduVijna AIEOS Backend repository are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this repository follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- GCI-I01 Generic Content pure-domain contracts under `src/aieos/domains/content/domain/` (identity, stewardship, origin, aggregate, version, schema registry, review decision, publication, domain errors) with focused unit tests. No persistence, HTTP, NATS, Temporal, or AI-provider behavior.
- GCI-I01R1: tenant/principal/correlation fields are stdlib UUID values (not Content-owned identity types); Python pin `>=3.14,<3.15`.
- GCI-I01R2: deeply immutable JSON ContentPayload; version_number==1 iff no parent; ARCHIVED withdraws active published_version_id.
- GCI-I02 Generic Content PostgreSQL schema `content.contents` / `content.content_versions` via Alembic, SQLAlchemy 2.0 mappings, FORCE RLS, transaction-local `aieos.tenant_id`, and ContentVersion immutability triggers. No repositories, HTTP, or GCI-I03+ persistence behavior.
- GCI-I03 application-owned Unit of Work and atomic immutable ContentVersion append with expected-revision concurrency and linear lineage. No HTTP, outbox, or later-slice tables.
- GCI-I04 FastAPI/Pydantic HTTP foundation: `POST/GET /api/v1/contents` and `GET /api/v1/contents/{content_id}` with RFC 9457 Problem Details, revision ETags, and opaque list cursors. Development/test mutation only; no production create, outbox, audit, Idempotency-Key, or ContentVersion HTTP.
- GCI-I05 ContentVersion append HTTP (`POST/GET .../versions`), mandatory If-Match, transactional Idempotency-Key for create and append, schema-registry payload validation, and `api.idempotency_records`. Still non-production: no outbox/audit intent.
- GCI-I06 Review Decision Foundation: submit/approve/request-changes/reject on an exact ContentVersion, immutable `content.review_decisions`, expected-revision concurrency, transactional Idempotency-Key, review authorization and comment-governance ports. Still non-production: no Temporal, outbox, audit, or publication.
- GCI-I07 Content Review Temporal Workflow: durable `workflow.workflow_start_intents` / `workflow.workflow_command_intents`, ContentReviewWorkflowV1 on task queue `aieos.content.review`, tenant-scoped start/command dispatchers, history replay gate. Still non-production: no outbox/audit, no production worker/dispatcher daemon.
- GCI-I08 Content Events + Transactional Outbox + NATS JetStream publication foundation: `integration.outbox_messages`, MutationEventContext CloudEvents 1.0 envelopes, Content UoW outbox inserts for the six emitted Content event types, tenant-scoped claim-fenced outbox dispatcher, nats-py JetStream publisher. Still non-production: required security-audit intent is absent; no consumer inbox, no publish/archive, no production NATS/dispatcher daemon.
- GCI-I09 Content Publish: immutable `content.publications`, `POST .../actions/publish` with If-Match + Idempotency-Key, published pointer update without a `PUBLISHED` stewardship state, `content.published.v1` outbox event, publication authorization/governance/asset ports. Still non-production: no audit intent, no archive, no `version_asset_refs`, no GET publications APIs.
- GCI-I09R1: publish IdempotencyOutcome stores `result_publication_id` and leaves `result_review_decision_id` NULL; replay-after-head-advance returns the original Publication/ETag.
- GCI-I10 Version Asset Refs: immutable `content.version_asset_refs`, append-time `asset_refs` with ResourceRef binding validation + canonical idempotency fingerprint, publish-time current-use asset governance over stored refs. Still non-production: no Asset storage/S3, no archive, no audit intent, no GET asset-ref routes, no event payload changes.
- GCI-I10R1: strict HTTP scalars for asset-ref revision/ordinal/required; shared pre-persistence duplicate `(role, ordinal)` validation; VersionAssetRef insert-failure UoW rollback proof.
- GCI-I11 AI Provenance & Generation Integration: typed allow-listed `AIGenerationProvenanceV1`, inbound AI materialization port (no provider SDK), DB defense-in-depth on `origin=AI` provenance JSONB, reuses shared append + Asset binding. Still non-production: no generation HTTP, no audit intent, no Teacher OS queue.
- GCI-I11R1: strict `schema_version` parser/DB checks reject bool/float/string coercions; only integral JSON `1` is valid.
- GCI-I12 Teacher OS Review Queue: read-only exact-version projection of `IN_REVIEW` current Content; no queue SoR table, no assignment/notifications, mutations unchanged and still non-production.
- GCI-I12R1: Review Queue integer limits outside 1..100 return 400 via queue-specific `ReviewQueueInvalidRequest`; malformed `limit` remains 422; Content list semantics unchanged.
- GCI-I13 Migration Adapter Foundation: typed `MigrationImportProvenanceV1`, durable `content.migration_import_records`, shared append IMPORT hardening, internal `ImportMigratedContentService` with GCI-G12 replay/conflict detection. Still non-production: no migration HTTP, no legacy connector, no audit intent, no production migration.
- GCI-I13R1: no-gap source serialization via session advisory lock spanning target rollback and FAILED evidence finalization.
- GCI-I14 adversarial TEST-ONLY suite under `tests/domains/content/adversarial/` (`pytest -m gci_i14`): identity/immutability, tenancy, review, AI, publication, workflow/events, migration, review queue, architecture abuse, and outbox atomicity cross-cuts. No production `src/` or `gcii140001` migration.
- SAI-I01 Security Audit contracts under `src/aieos/platform/security/audit/`: `AuditRecordId`, typed actions/channels, `SecurityMutationAuditContext`/`Record`, canonical builder, insert-only repository port. No DB table, no Content mutation wiring, still non-production.
- SAI-I02 PostgreSQL security audit ledger: `security.audit_records` via `saii020001`, distinct `AIEOS_SECURITY_SCHEMA_OWNER_ROLE`, FORCE RLS INSERT-only policy, immutability triggers, SQLAlchemy insert-only repository. No Content mutation wiring, still non-production.
- SAI-I03 Generic Content API transactional security-audit integration: create / human append / review submit+decide / publish write one `security.audit_records` row in the same Content UoW transaction as business + outbox (+ workflow intent) + idempotency. AI materialization, migration import, and workflow-origin audit remain SAI-I04; production mutation still NOT AUTHORIZED. No new migration.
- SAI-I04 AI materialization + controlled migration transactional security-audit integration: `content.ai.materialize` and `content.migration.import` write one audit row in the same Content UoW transaction. Workflow-origin Content mutation remains absent/N/A. Production mutation and production migration still NOT AUTHORIZED. No new migration.
- SAI-I05 final adversarial security-audit / transaction / tenancy / implementation-readiness gate (tests + docs only): proves all currently implemented Generic Content committed mutations keep authority + business + outbox (+ workflow/idempotency) + security audit in one PostgreSQL transaction. AIEOS Security Audit Implementation Baseline SAI-I01–SAI-I05 = IMPLEMENTATION-BASELINE COMPLETE (NON-PRODUCTION). Production role/credential/environment provisioning not verified. Production mutation, production migration, and production deployment remain NOT AUTHORIZED. No production `src/` or migration changes.

### Changed

- SAI-I02R1: `security.related_resource_refs_are_valid` duplicate identity uses normalized UUID text; `resource_revision` accepts only canonical non-negative integer JSON (`^[0-9]+$`), rejecting `1.0` and out-of-range values. Head remains `saii020001`.
- GCI-I02R1: ordinary runtime has no DELETE on `contents` and no UPDATE/DELETE on `content_versions`; Alembic DDL runs under an explicit NOLOGIN schema-owner role distinct from migrator and runtime.
- GCI-I02R2: Alembic offline SQL emits `SET LOCAL ROLE` for the configured schema-owner before Generic Content DDL, matching online ownership.
- GCI-I03R1: SQLAlchemy/DBAPI/psycopg exceptions are translated at the Generic Content persistence boundary into technology-neutral application errors.
- GCI-I05R1: create Idempotency-Key replay returns the original established create result (revision 0) rather than current Content state; catalog validation runs only for new creates. Privilege contract now records `api` schema runtime grants.
- GCI-I05R2: OpenAPI advertises only operation-applicable error statuses (If-Match/412/428 only on version append); schema validators map only `InvalidPayloadError` to 422, and unexpected validator defects reach sanitized 500.

### Deprecated

- Nothing yet.

### Removed

- Nothing yet.

### Fixed

- GCI-I08R1: outbox `dispatch_once` bounds `EventPublisher.publish` with `publish_timeout_seconds`; timeout maps to retryable `nats_unavailable` without weakening claim fencing or replacing event identity.
- GCI-I06R1: ContentVersion append stewardship eligibility is enforced only in `append_version_in_uow` (DRAFT/GENERATED/APPROVED; IN_REVIEW/ARCHIVED raise `ContentVersionAppendNotAllowed` before insert). HTTP no longer duplicates that rule. `advance_current_version` requires the locked head's stewardship state in the UPDATE predicate.
- GCI-I07R1: workflow intent finalization is claim-fenced (`status=CLAIMED` + `claimed_by` + `attempt_count`); WorkflowAlreadyStarted reconciliation uses server-stored start history/input without requiring a worker.

### Security

- GCI-I02R1 withholds ordinary-runtime DELETE/UPDATE privileges that would otherwise authorize physical purge or historical ContentVersion mutation.

## [0.1.0] - 2026-08-13

### Added

- Repository foundation: README, CONTRIBUTING, CODEOWNERS, LICENSE (Apache-2.0), VERSION, changelog, and GitHub issue/PR templates aligned with `eduvijna-architecture` and `eduvijna-product`.

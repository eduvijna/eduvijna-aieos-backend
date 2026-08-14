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

### Changed

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

- GCI-I06R1: ContentVersion append stewardship eligibility is enforced only in `append_version_in_uow` (DRAFT/GENERATED/APPROVED; IN_REVIEW/ARCHIVED raise `ContentVersionAppendNotAllowed` before insert). HTTP no longer duplicates that rule. `advance_current_version` requires the locked head's stewardship state in the UPDATE predicate.
- GCI-I07R1: workflow intent finalization is claim-fenced (`status=CLAIMED` + `claimed_by` + `attempt_count`); WorkflowAlreadyStarted reconciliation uses server-stored start history/input without requiring a worker.

### Security

- GCI-I02R1 withholds ordinary-runtime DELETE/UPDATE privileges that would otherwise authorize physical purge or historical ContentVersion mutation.

## [0.1.0] - 2026-08-13

### Added

- Repository foundation: README, CONTRIBUTING, CODEOWNERS, LICENSE (Apache-2.0), VERSION, changelog, and GitHub issue/PR templates aligned with `eduvijna-architecture` and `eduvijna-product`.

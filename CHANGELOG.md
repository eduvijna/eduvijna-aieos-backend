# Changelog

All notable changes to the EduVijna AIEOS Backend repository are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this repository follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- TOS-DEV04-I06 — `PrepareTeachingWorkService` orchestration and recovery
  (ADR-AIEOS-052): GenerationRun Fence A/B claim + idempotency, I04 generation,
  I05 whole-kit Educational Quality gate, I03 atomic six-artifact materialization,
  Content-first exact-six crash/replay reconciliation with singular `result_*`
  fields remaining NULL. Additive application service only — no HTTP/OpenAPI,
  migration, runtime activation, live provider, Temporal/Agent/MCP.
- TOS-DEV04-I05 — preparation Educational Quality V1 and cross-artifact coherence
  baseline (ADR-AIEOS-052): provider-independent
  `evaluate_preparation_educational_quality_v1` over final
  `PreparationArtifactPayloadsV1` with hard checks (schema revalidation, shared
  objectives, component mappings, question-ID integrity, Answer Key completeness
  and reference integrity, ordered objective consistency, structural topic
  lineage, whole-kit unsupported-alignment scan, teacher notes) plus inherited
  prefixed DEV03 worksheet EQ. Extracts shared public
  `find_unsupported_alignment_claim` without changing DEV03 semantics. No
  provider call, Content materialization, GenerationRun orchestration, API,
  frontend, migration, or capability wiring.
- TOS-DEV04-I04 — `GeneratePreparationKitCapability` and deterministic Answer Key
  builder (ADR-AIEOS-052): one provider-neutral `StructuredModelGateway` call to
  `PreparationKitV1`, then pure transforms to LessonPlanV1 / WorksheetV1 / QuizV1 /
  HomeworkV1 / AnswerKeyV1 / TeacherNotesV1 in-memory draft. Shared capability ID
  `education.generate_preparation_kit` centralized in platform capabilities; I03
  imports it without behavior change. No Educational Quality, Content persistence,
  GenerationRun orchestration, PrepareTeachingWorkService, API/OpenAPI, frontend,
  migration, live provider call, or runtime registration.
- TOS-DEV04-I03R1 — harden final LessonPlanV1 / QuizV1 / HomeworkV1 learning-
  objective semantic invariants: whitespace-only objective id/text are rejected
  at the Content payload boundary without changing WorksheetV1 / LearningObjectiveV1
  (DEV03) or the accepted I03 atomic materialization service. No migration, API,
  frontend, or provider call.
- TOS-DEV04-I03 — atomic six-artifact Generic Content materialization for review
  (ADR-AIEOS-052): final LessonPlanV1 / QuizV1 / HomeworkV1 / TeacherNotesV1
  payload contracts with education schema adapters and typed audience metadata;
  Generic Content AI provenance materialization accepts V1 or V2; composite
  CreateAIPreparationArtifactsForReviewService commits six Contents + versions +
  IN_REVIEW admissions in one Content UoW / one transaction (6/6 or 0/6). Reuses
  WorksheetV1 and AnswerKeyV1 unchanged. No migration, Answer Key builder,
  preparation capability, Educational Quality, PrepareTeachingWorkService,
  /actions/prepare, OpenAPI, frontend, provider call, or production Content
  activation.
- TOS-DEV04-I02R1 — cross-revision stale GenerationRun isolation for the DEV03
  worksheet compatibility path: a stale RUNNING run bound to Work revision R0
  may be reconciled/finalized as R0, but must never return R0 artifacts as the
  successful outcome of an R1 request. Fence B release then allows the R1 claim
  under the existing bounded retry. No migration, API, frontend, or provider
  call.
- TOS-DEV04-I02 — multi-artifact ContentVersion uniqueness and capability/
  revision-aware GenerationRun fences (ADR-AIEOS-052 persistence substrate).
  Alembic `tosd040001` adds strict DB AI provenance V2 validation with a
  version-aware dispatcher (V1+V2), replaces the V1-only ContentVersion CHECK,
  evolves AI ContentVersion uniqueness to V1 (tenant+run) / V2
  (tenant+run+artifact_kind), and replaces the work-only GenerationRun fence
  with Fence A (work+revision+capability for RUNNING|SUCCEEDED) and Fence B
  (work+capability for RUNNING). Adds plural provenance lookup by
  GenerationRun while preserving DEV03 V1 singular query semantics. No
  preparation capability, six-artifact materialization, API, frontend, or
  provider call.
- TOS-DEV04-I01 — typed `PreparationKitV1` provider-neutral structured-output
  contract with component drafts (lesson plan, worksheet, quick quiz, homework,
  teacher notes) and `AnswerKeyV1` contract-only payload; strict cross-field
  objective and question-ID validation; `AIGenerationProvenanceV2` with
  required `artifact_kind` and version-aware provenance JSON
  serialize/parse while preserving exact V1 compatibility. No database
  migration, API, frontend, provider call, or Content catalog activation.
- TOS-DEV02 Lane B — Teaching Work durable aggregate and Today's Mission
  projection. New `teaching` bounded context (`src/aieos/domains/teaching/**`)
  with the `TeachingWork` aggregate, create/refine/query/mission application
  services, SQLAlchemy persistence, and HTTP v1 operations
  `teaching_work_create`, `teaching_work_list`, `teaching_work_get`,
  `teaching_work_refine`, and `teacher_os_today_mission`. Adds Alembic
  migration `tosd020001` creating the `teaching` schema, `teaching.works`
  (UUIDv7 PK, per-tenant indexes, `teaching.current_tenant_id()`, ENABLE +
  FORCE RLS with a tenant isolation policy), and moves the expected Alembic
  head from `adra045001` to `tosd020001`. Teaching Intent stays a request
  that enters Work creation — there is no `teaching_intents` table — and
  Mission is recomputed on every read from the Review Queue projection plus
  durable Work rows, so there is no mission table. Refine uses `If-Match`
  optimistic concurrency and `Idempotency-Key` replay through the existing
  platform idempotency records. Adds the NON_PRODUCTION reference scenario
  `src/aieos/development/teacher_os_teaching_work_scenario.py` with an
  explicit loader under `tools/development/` (never seeded on startup),
  PostgreSQL-backed and adversarial `tos_dev02` tests, and
  `docs/TOS-DEV02-LANE-B.md`. No AI generation, agents, or MCP surface.
- TOS-DEV01 Lane B — NON_PRODUCTION Teacher OS Review Queue development
  reference scenario (`src/aieos/development/**`, explicit loader under
  `tools/development/`), plus PostgreSQL-backed spine proofs in
  `tests/development/test_tos_dev01_review_spine.py`. Reuses existing
  Generic Content / Review Queue services and `test.generic` fixture schema;
  no migration; no production auth/runtime composition change.
- WPI-OCI-I01R1 Backend OCI provenance hardening + exact-head CI recovery
  (ADR-AIEOS-051): PR CI builds/validates exact `pull_request.head.sha`
  (never synthetic merge SHA); removes `--allow-dirty`; derives receipt
  Python/uv/base-image/OS/user/Cmd/labels from actual image/Dockerfile;
  verifier binds VERSION/Dockerfile/uv.lock hashes to current source;
  exact `10001:10001` user and exact fail-closed default command; closed
  OCI label receipt schema; fail-closed image-config auth scan; Dockerfile
  revision build-args without `unknown` defaults. Credential-free only;
  DOCR publication, DigitalOcean credentials, registry login/push,
  WPI-OCI-P01, TV01 App CREATE, and production deployment remain
  **NOT AUTHORIZED**. TV01 remains **AUTHORIZED BUT PAUSED ON OCI
  MANIFEST DIGEST**. Supersedes unmerged PR #19 recovery path.
- WPI-OCI-I01 Backend production OCI source foundation (ADR-AIEOS-051):
  `deploy/oci/Dockerfile.backend-runtime` (Python 3.14.7 / uv 0.12.4 /
  linux/amd64 / non-root 10001:10001 / fail-closed default exit 64),
  stdlib provenance tooling under `tools/release/backend_oci_*.py`,
  credential-free validator `tools/runtime/run_backend_oci_validation.sh`,
  architecture/provenance tests, CI job `backend-production-oci`, and
  `docs/WPI-OCI-I01-BACKEND-PRODUCTION-OCI.md`. Source-identity note:
  `8f4dd172…` is I01 implementation BASE only; publishable revision is the
  post-I01 merged Backend SHA. `Dockerfile.api-runtime-probe` remains
  NON_PRODUCTION and untouched. Credential-free local/CI validation only;
  DOCR publication, DigitalOcean credentials, registry login/push,
  WPI-OCI-P01, TV01 App CREATE, and production deployment remain
  **NOT AUTHORIZED**. WPI-AP-DP-TV01 remains **AUTHORIZED BUT PAUSED ON
  OCI MANIFEST DIGEST**.
- PED-I12 production WORKFLOW dispatcher runtime **source** (ADR-AIEOS-045/047):
  fail-closed `WorkflowDispatcherRuntimeConfig` with architecture-frozen
  `AIEOS_WORKFLOW_DISPATCHER_TEMPORAL_*` env names (worker `AIEOS_TEMPORAL_*`
  never accepted as fallback), dedicated dispatcher Engine + dual
  `to_regprocedure` authority probe, START/COMMAND candidate repositories,
  distinct TLS Temporal client factory with outer complete-connect timeout,
  task-queue operation fence (`aieos.content.review` exact), fair dual-stream
  daemon reusing existing start/command dispatchers + delivery reconciliation,
  executable `python -m aieos.platform.runtime.entrypoints.workflow_dispatcher_main`,
  focused `ped_i12` tests, CI job `workflow-dispatcher-runtime`, and
  `docs/PED-I12-PRODUCTION-WORKFLOW-DISPATCHER-RUNTIME.md`. Production Temporal
  Cloud access, Namespace/service-account/API-key mutation, production DB
  migration, candidate-reader provisioning, deployment, and production
  dispatcher execution remain NOT AUTHORIZED.
- PED-I11R1 corrective runtime gates on the EVENT dispatcher source: outer asyncio
  deadline for complete initial NATS establishment; Temporal-style shutdown-grace
  supervision of in-flight daemon work; exact `to_regprocedure` OID resolution for
  `integration.list_outbox_dispatch_candidates(integer,timestamp with time zone)`;
  disposable TLS proof through `connect_event_dispatcher_nats` with verified N9
  cleanup postcondition. No ADR redesign, migration, OpenAPI, or production authority.
- PED-I11 production EVENT dispatcher runtime source (ADR-AIEOS-025/045/046): fail-closed
  `EventDispatcherRuntimeConfig`, in-memory JWT/NKey `.creds` callbacks, TLS NATS connection
  factory, READ-ONLY database authority probe, `SqlAlchemyOutboxCandidateRepository` over
  `integration.list_outbox_dispatch_candidates`, fair round-robin daemon, executable
  `python -m aieos.platform.runtime.entrypoints.event_dispatcher_main`, expected-stream
  PubAck enforcement for `AIEOS_EVENTS_PROD`, `nats-py[nkeys]` dependency, focused `ped_i11`
  tests, disposable CI `event-dispatcher-runtime` JWT/NKey proof, and
  `docs/PED-I11-PRODUCTION-EVENT-DISPATCHER-RUNTIME.md`. Production NATS/DB access,
  credentials/stream creation, candidate-reader provisioning, migration execution,
  WORKFLOW dispatcher, and production dispatcher execution remain NOT AUTHORIZED.
- ADR-AIEOS-045 database candidate-authority: Alembic `adra045001` role-scoped RLS, candidate-reader grants/functions/indexes, architecture + PostgreSQL 18 acceptance tests, and CI `postgresql-candidate-authority` handshake against Infrastructure pin `1249634403cacd9caec4ba48b72821e629b222f5`. Production migration is not executed, production candidate-reader roles are not provisioned, and the dispatcher daemon is not implemented or deployed.
- Production runtime executable entrypoints (Phase 1): `python -m aieos.platform.runtime.entrypoints.api_main` and `python -m aieos.platform.runtime.entrypoints.temporal_worker_main` with fail-closed configuration, governed dependency composition, PED-I06 Uvicorn serving, and Temporal SDK graceful shutdown. Explicit exclusions remain: event/workflow dispatcher daemons, tenant enumeration, scheduler/reconciliation runtime, backup worker, App Platform spec, OCI production promotion, cloud access, and commercial release. Production deployment and commercial release remain NOT AUTHORIZED.
- Phase-1R correction on PR #15: Temporal worker observes both `Worker.run()` completion and shutdown signals; logging formatter no longer requires custom LogRecord fields; production Content catalog/registry remain intentionally empty (no `test.generic` promotion).

### Changed

- PED-I10B8 AIStor live-conformance correction (NON_PRODUCTION): upgrade
  boto3/botocore to `1.43.57`/`1.43.57`; wrap PutObject Body in a read-only
  infrastructure streaming facade; require `ChecksumAlgorithm="SHA256"` on the
  single PutObject; discriminate ambiguous AIStor HeadObject 404 via
  `GetBucketLocation` plus one stability HEAD (no ListBucket/ListObjects).
  Documents future runtime `s3:GetBucketLocation` permission and ListBuckets
  residual. No ADR change, Asset HTTP, OpenAPI, migration, production
  composition, credentials, or cloud resources.

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
- PED-I01 production/staging API runtime configuration and composition foundation under `src/aieos/platform/runtime/`: typed fail-closed config, release identity, role separation, secret-safe handling, and explicit `compose_api_application` dependency bundle. Classified as production-readiness foundation only. No ASGI server, health endpoints, mutation activation, CI/CD, containers, or DB migration. Production deployment, mutation, and migration remain NOT AUTHORIZED.
- PED-I02 API runtime database/readiness foundation: shared `postgresql+psycopg` Engine factory with bounded connect timeout, fail-closed PostgreSQL identity/privilege/schema-owner/PG18/Alembic-head readiness probe, operational `/livez` and `/readyz` (excluded from OpenAPI), and narrow `public.alembic_version` SELECT for readiness metadata only. No ASGI server, mutation activation, CI/CD, containers, or new migration. Production deployment, mutation, and migration remain NOT AUTHORIZED.
- PED-I03 fail-closed API mutation activation safety interlock: release-bound `ENABLED`/`DISABLED` gate installed by `compose_api_application`, blocking the frozen Content mutation operation inventory before UoW with RFC 9457 `mutations_not_activated` (503). Reads and PED-I02 health remain available when activation is missing, invalid, mismatched, or broken. No feature-flag platform, no ASGI server, no DB migration. Production deployment, mutation, and migration remain NOT AUTHORIZED.
- PED-I04 CI quality gates and immutable verified build bundle foundation: GitHub Actions `quality-gate` + main-only `verified-build`, locked uv install, compile/pytest gates, SHA-pinned actions, and a NON_PRODUCTION wheel/sdist tar bundle with machine-readable manifest. No deployment, OCI, PyPI/GHCR, ASGI server, or mutation activation change. Production deployment, mutation, and migration remain NOT AUTHORIZED.
- PED-I06 ASGI server and NON_PRODUCTION OCI runtime viability foundation: Uvicorn 0.51 ASGI config module, digest-pinned Astral uv OCI probe image with Python 3.14.7, non-root execution, locked non-dev install, and CI `oci-runtime-probe` hardened HTTP smoke. No product API composition, no SecurityContextResolver, no registry publication, no target cloud. Production deployment, mutation, and migration remain NOT AUTHORIZED.
- PED-I07 trusted request identity and current-tenant SecurityContext foundation: explicit `RequestIdentityAuthenticator` port, immutable `TrustedRequestIdentity`, current `CurrentTenantAccessAuthority`, `CurrentAuthoritySecurityContextResolver`, and fail-closed 401/403/503 Problem Details wiring into `create_app` / `ApiRuntimeDependencies`. No production IdP/JWT/OIDC authenticator, no policy engine, no OpenAPI auth scheme, no migration. Production deployment, mutation, and migration remain NOT AUTHORIZED.
- PED-I08 concrete production JWT Bearer `RequestIdentityAuthenticator` (ADR-AIEOS-030): RS256 + configured HTTPS JWKS, exact issuer/audience, required `sub`/`client_id`/`jti`/`exp`/`iat`, canonical `https://eduvijna.com/claims/aieos/principal_id` UUID claim → `TrustedRequestIdentity(principal_id)` only; typed `AIEOS_AUTH_ISSUER`/`AUDIENCE`/`JWKS_URI`; PyJWT 2.x + cryptography; additive OpenAPI `AIEOSBearerAuth`. No policy engine, no principal mapping tables/migrations, no login UX. Production deployment, mutation, and migration remain NOT AUTHORIZED.
- PED-I09 production Authorization Kernel (ADR-AIEOS-031): embedded ALLOW/DENY default-deny kernel over PostgreSQL `security` authority SoR (`principals`, `tenants`, `tenant_memberships`, `capability_grants`) via Alembic `pedi090001`; short authority read transactions with transaction-local `aieos.tenant_id` RLS query scope; `KernelCurrentTenantAccessAuthority` plus Content review/publication/AI/migration capability adapters; no roles, wildcards, delegation, break-glass, external policy engine, or control-plane APIs. OpenAPI unchanged. Production deployment, mutation, and migration remain NOT AUTHORIZED.
- PED-I10A production governance adapter foundation (ADR-AIEOS-032): platform `GovernanceUnavailableError` (503 `governance_unavailable`); deterministic Review Comment Policy V1; baseline Publication Governance V1; provider-neutral `AssetUseAuthority` / `AssetUseAssessment` contract; Content Asset binding and current-use adapters over that contract with explicit handled resource types. No concrete Asset/File provider, no migration (head remains `pedi090001`), no dependency delta, OpenAPI unchanged. Production deployment, mutation, and migration remain NOT AUTHORIZED. PED-I10B remains NOT AUTHORIZED.
- PED-I10B1 Asset/File domain contracts (ADR-AIEOS-033): pure Python Asset-owned identities, exact V1 resource-type vocabulary (`asset.image|document|audio|video`), lifecycle/quarantine/safety vocabularies, immutable `Asset` / `AssetRevision` / `AssetRevisionState` snapshots. No persistence, BlobStore, AssetUseAuthority implementation, HTTP, migration, or dependency delta. Alembic head remains `pedi090001`. OpenAPI unchanged. PED-I10B2+ and production changes remain NOT AUTHORIZED.
- PED-I10B2 Asset PostgreSQL System of Record (ADR-AIEOS-033): domain-owned schema `asset` with `assets`, `asset_revisions`, `asset_revision_states`, and `deletion_evidence`; SQLAlchemy 2 Core metadata; Alembic `pedi10b2001` (down_revision `pedi090001`); FORCE RLS via transaction-local `aieos.tenant_id`; dedicated Asset schema-owner role; immutable revision facts and deletion evidence. No repository/UoW, BlobStore, AssetUseAuthority implementation, Content table change, API/runtime composition, or dependency delta. OpenAPI unchanged. Classification remains NON_PRODUCTION. PED-I10B3+ and production migration/mutation/deployment remain NOT AUTHORIZED.
- PED-I10B3 BlobStore ingest and reconciliation foundation (ADR-AIEOS-033): provider-neutral BlobStore / BlobInventory ports, UUIDv7 server-generated opaque storage keys, PreparedBlob pre-persistence physical ingest, explicit PostgreSQL/BlobStore non-atomicity (no automatic compensation delete), deterministic MATCH/MISSING/INTEGRITY_MISMATCH reconciliation, and non-destructive orphan candidates. No production storage-provider implementation, repository/UoW, AssetUseAuthority implementation, migration, API/runtime composition, or dependency delta. Alembic head remains `pedi10b2001`. OpenAPI unchanged. Classification remains NON_PRODUCTION. PED-I10B4+ remains unauthorized.
- PED-I10B4 Asset current-use authority provider (ADR-AIEOS-034 FROZEN / APPROVED): concrete `AssetCurrentUseAuthority` / `PostgresAssetUseAuthority` behind `AssetUseAuthority.assess_use`, eleven-value rejection vocabulary (`BYTES_PURGED` / `BYTES_MISSING` / `INTEGRITY_MISMATCH`), RLS-safe `NOT_FOUND`, BlobStore.inspect physical observation, bounded cross-store positive-result stability retries. No migration (head remains `pedi10b2001`), no OpenAPI delta, no runtime dependency delta, no production BlobStore, no API/runtime composition. Classification remains NON_PRODUCTION. PED-I10B5+ and production migration/mutation/deployment remain NOT AUTHORIZED.
- PED-I10B5 Asset mutation and revision activation foundation (ADR-AIEOS-035 FROZEN / APPROVED): create Asset, register immutable AssetRevision with pending revision-state, explicit revision activation with inspect-then-lock cross-store stability, lifecycle/quarantine/safety transitions, and Asset write Unit of Work over existing PED-I10B2 tables. No migration (head remains `pedi10b2001`), no OpenAPI delta, no runtime dependency delta, no production BlobStore, no Asset API, no purge/`bytes_purged`/`deletion_evidence`, no production composition. Classification remains NON_PRODUCTION. PED-I10B6+ and production migration/mutation/deployment remain NOT AUTHORIZED.
- PED-I10B6 Asset authorization and transactional security audit (ADR-AIEOS-036 / ADR-AIEOS-036R1 FROZEN / APPROVED): exact six Asset capabilities, Authorization Kernel-backed mutation authorization before the first Asset Unit of Work, ten `asset.*` security-audit actions, ADR-AIEOS-036R1 stable-primary `resource_revision=None` with aggregate revision in before/after fields, same-transaction `security.audit_records` insert, and Alembic `pedi10b6001` (down_revision `pedi10b2001`) with fail-closed downgrade when Asset audit evidence exists. No Asset HTTP, events/outbox, BlobStore provider, purge/`bytes_purged`/`deletion_evidence`, or production composition. OpenAPI unchanged. Classification remains NON_PRODUCTION. Schema-owner readiness and architecture-catalogue synchronization remain open. Production mutation/deployment remain NOT AUTHORIZED.
- PED-I10B8 AIStor BlobStore adapter and exact-length streaming contract (ADR-AIEOS-033/039/040R1/042/043 FROZEN / APPROVED): provider-neutral `BlobStore.create` / `BlobIngestPreparer.prepare` gain exact declared `byte_size`; MinIO AIStor adapter via boto3/botocore `1.40.21`/`1.40.76` low-level single PutObject (`IfNoneMatch=*`, `ContentLength`) plus post-write HEAD checksum observation (Base64 ChecksumSHA256 → lowercase hex); create retries `total_max_attempts=1`; no multipart/transfer helpers; delete method present without application call sites or runtime delete authority. No Asset HTTP, OpenAPI delta, migration (head remains `pedi10b6001`), BlobInventory provider, production composition, credentials, or cloud resources. Classification remains NON_PRODUCTION. Live AIStor conformance and production mutation/deployment remain NOT AUTHORIZED.

### Changed

- GCI-I05R3: concurrent ContentVersion append with distinct Idempotency-Key and the same If-Match produces exactly one 201 and one 412 `resource_revision_conflict`; `get_head_for_update` locks `content.contents` then separately reads the current ContentVersion so READ COMMITTED cannot return a mixed `current_version_id`/`version_number` projection. No migration, OpenAPI, or dependency change.
- PED-I10B5R1: activation requires `candidate.aggregate_revision == expected_aggregate_revision` before `BlobStore.inspect`, and post-inspect stability includes `locked.aggregate_revision == candidate.aggregate_revision`, so a future expected revision cannot become a successful activation if aggregate authority advances during inspect. No migration, OpenAPI, uv.lock, Content, API, purge, or composition change. Classification remains NON_PRODUCTION.
- PED-I09R2: corrupt / unrecognized authority status strings raise `AuthorizationUnavailableError` (503) rather than ordinary DENY (403); valid `SUSPENDED`/`DISABLED`/`REVOKED` remain DENY.
- PED-I09R1: reject wildcard capability identifiers (`*` / `content.*` / etc.) at DB CHECK and AuthorizationKernel construction/decision; Content capability constants owned solely by `domains/content/application/ports.py` (no parallel definitions in generic `decisions.py`).
- PED-I08R1: JWT header `typ` is mandatory and must be exactly `at+jwt` (reject absent/`JWT`/`ID`/other); no `application/at+jwt` media-type change. OpenAPI `AIEOSBearerAuth` unchanged.
- PED-I08: ADR-AIEOS-030-authorized additive OpenAPI Bearer security scheme (`AIEOSBearerAuth`); not uncontrolled API drift. Auth env vars required by STAGING/PRODUCTION `ApiRuntimeConfig` load. PED-I07 architecture tests narrowly advanced to confine PyJWT to `jwt_bearer.py`.
- PED-I07: advances SecurityContext resolution to consume trusted identity + requested tenant with current tenant-access authority; OpenAPI product surface unchanged.
- PED-I06: advances PED-I01/PED-I04 architecture tests narrowly so uvicorn is permitted only in the authorized ASGI runtime module and a governed NON_PRODUCTION OCI probe may exist; registry push, deployment, production naming, mutation activation, and GitHub Release remain forbidden.
- PED-I04R1: verified-bundle verifier enforces immutable manifest `application_version` (`0.1.0`) and `repository` (`eduvijna/eduvijna-aieos-backend`); tampered identity fields fail verification. No workflow, OpenAPI, or runtime change.
- PED-I04: normalize `contracts/openapi/aieos-v1.json` to LF via `.gitattributes` and update the frozen OpenAPI SHA256 to the LF-byte digest so Linux CI and Windows agree. Product OpenAPI semantics are unchanged.
- PED-I01R1: STAGING/PRODUCTION `AIEOS_RUNTIME_DATABASE_URL` accepts only the exact SQLAlchemy driver `postgresql+psycopg` (Psycopg 3). Bare `postgresql://` and alternate dialects are rejected fail-closed. No dependency change.
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

# Production Runtime Entrypoints

**Status:** IMPLEMENTED (source) — production deployment, cloud provisioning, App
Platform specification, and commercial release remain **NOT AUTHORIZED**.

**Governed base:** backend `0040e1121f19f0b6177e87a736d32f8ccc926440`

## Authorized executables (Phase 1)

| Workload | Command |
|----------|---------|
| HTTP API | `python -m aieos.platform.runtime.entrypoints.api_main` |
| Content Review Temporal worker | `python -m aieos.platform.runtime.entrypoints.temporal_worker_main` |
| EVENT dispatcher | `python -m aieos.platform.runtime.entrypoints.event_dispatcher_main` |
| WORKFLOW dispatcher | `python -m aieos.platform.runtime.entrypoints.workflow_dispatcher_main` |

Importing these modules has **no runtime side effects**. Configuration and external
connections occur only when `main()` executes.

## Explicit exclusions (still gated)

- Event dispatcher daemon — **source implemented (PED-I11); production execution/deployment NOT AUTHORIZED**
- Workflow dispatcher daemon — **source implemented (PED-I12); production execution/deployment NOT AUTHORIZED**
- Tenant enumeration / cross-tenant scanning
- Scheduled / periodic reconciliation runtime
- Asset backup worker
- App Platform specification or sizing freeze
- OCI production promotion / deployment

## EVENT dispatcher startup sequence (PED-I11)

1. `load_event_dispatcher_runtime_config_from_process_environment()`
2. `create_event_dispatcher_engine(config)`
3. READ-ONLY database authority probe
4. Parse `AIEOS_EVENT_DISPATCHER_NATS_CREDENTIALS` in memory
5. TLS NATS connect with `user_jwt_cb` / `signature_cb`
6. Candidate discovery + `ContentOutboxDispatcher.dispatch_once(tenant_id)` fair loop
7. SIGTERM/SIGINT → drain NATS, wipe credentials, dispose Engine

See `docs/PED-I11-PRODUCTION-EVENT-DISPATCHER-RUNTIME.md`.

## WORKFLOW dispatcher startup sequence (PED-I12)

1. `load_workflow_dispatcher_runtime_config_from_process_environment()`
2. `create_workflow_dispatcher_engine(config)`
3. READ-ONLY dual-candidate-function authority probe
4. Distinct WORKFLOW_DISPATCHER Temporal `Client.connect` (TLS + dispatcher API key; outer complete-connect timeout)
5. `TemporalClientReviewGateway` + existing START/COMMAND dispatchers
6. START + COMMAND candidate discovery + fair dual-stream daemon
7. SIGTERM/SIGINT → shutdown grace for in-flight pass → dispose Engine

See `docs/PED-I12-PRODUCTION-WORKFLOW-DISPATCHER-RUNTIME.md`.

**PED-I12 Backend source ≠ production WORKFLOW dispatcher activation.**

Production operating cadence/batch values remain **deployment-gated** (not frozen here).

## API startup sequence

1. `load_api_runtime_config_from_process_environment()`
2. `create_api_runtime_engine(config)`
3. `compose_api_runtime_dependencies(engine, config)`
4. `compose_api_application(config, dependencies)` (PED-I03 interlock installed)
5. `serve_api_application(app)` (PED-I06 Uvicorn defaults)
6. `engine.dispose()` in `finally`

## Temporal worker startup sequence

1. `load_temporal_worker_runtime_config_from_process_environment()`
2. `Client.connect(..., tls=True, api_key=...)`
3. `create_content_review_worker(client)` on task queue `aieos.content.review`
4. `worker.run()` until SIGTERM/SIGINT → `worker.shutdown()` within grace

## Production Content catalog/registry posture

Phase-1 production runtime composition uses an **intentionally empty** Content-type
catalog and schema registry. No educational production Content type is registered
yet. Test fixtures and event contract samples are **not** production registry
authority. Future production Content mutation activation requires separately
governed production Content-type/schema registration.

## Configuration categories

### Shared release / environment

- `AIEOS_DEPLOYMENT_ENVIRONMENT`
- `AIEOS_RELEASE_VERSION`
- `AIEOS_GIT_SHA`
- `AIEOS_BUILD_ID`
- `AIEOS_ARTIFACT_DIGEST`

### API-only (PED-I01 + PED-I08)

See `docs/PED-I01-PRODUCTION-RUNTIME-CONFIG-CONTRACT.md` and
`docs/PED-I08-PRODUCTION-AUTHENTICATION-CONTRACT.md`.

### AIStor (Asset current-use governance for API composition)

- `AIEOS_AISTOR_ENDPOINT_URL`
- `AIEOS_AISTOR_BUCKET`
- `AIEOS_AISTOR_REGION`
- `AIEOS_AISTOR_ACCESS_KEY_ID`
- `AIEOS_AISTOR_SECRET_ACCESS_KEY`
- Optional: `AIEOS_AISTOR_CONNECT_TIMEOUT_SECONDS`, `AIEOS_AISTOR_READ_TIMEOUT_SECONDS`, `AIEOS_AISTOR_CA_BUNDLE_PATH`

### Temporal worker-only

- `AIEOS_TEMPORAL_TARGET_HOST`
- `AIEOS_TEMPORAL_NAMESPACE`
- `AIEOS_TEMPORAL_API_KEY`
- `AIEOS_TEMPORAL_CONNECT_TIMEOUT_SECONDS`
- `AIEOS_TEMPORAL_SHUTDOWN_GRACE_SECONDS`

STAGING/PRODUCTION Temporal connections require TLS (`tls=True`). Plaintext is forbidden.

### WORKFLOW dispatcher-only (PED-I12; distinct from worker)

- `AIEOS_WORKFLOW_DISPATCHER_TEMPORAL_TARGET_HOST`
- `AIEOS_WORKFLOW_DISPATCHER_TEMPORAL_NAMESPACE`
- `AIEOS_WORKFLOW_DISPATCHER_TEMPORAL_API_KEY`
- `AIEOS_WORKFLOW_DISPATCHER_TEMPORAL_CONNECT_TIMEOUT_SECONDS`
- plus DB/daemon cadence variables documented in `docs/PED-I12-PRODUCTION-WORKFLOW-DISPATCHER-RUNTIME.md`

Worker `AIEOS_TEMPORAL_*` variables are **not** dispatcher credential fallback.

## Commercial / provisioning

Commercial blocker remains **RED / IN FORCE**. Workload provisioning on App Platform
or cloud infrastructure is **NOT AUTHORIZED** by this slice.

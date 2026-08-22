# Production Runtime Entrypoints

**Status:** IMPLEMENTED (source) — production deployment, cloud provisioning, App
Platform specification, and commercial release remain **NOT AUTHORIZED**.

**Governed base:** backend `0040e1121f19f0b6177e87a736d32f8ccc926440`

## Authorized executables (Phase 1)

| Workload | Command |
|----------|---------|
| HTTP API | `python -m aieos.platform.runtime.entrypoints.api_main` |
| Content Review Temporal worker | `python -m aieos.platform.runtime.entrypoints.temporal_worker_main` |

Importing either module has **no runtime side effects**. Configuration and external
connections occur only when `main()` executes.

## Explicit exclusions (still gated)

- Event dispatcher daemon
- Workflow dispatcher daemon
- Tenant enumeration / cross-tenant scanning
- Scheduled / periodic reconciliation runtime
- Asset backup worker
- App Platform specification or sizing freeze
- OCI production promotion / deployment

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

## Commercial / provisioning

Commercial blocker remains **RED / IN FORCE**. Workload provisioning on App Platform
or cloud infrastructure is **NOT AUTHORIZED** by this slice.

# PED-I11 — Production EVENT Dispatcher Runtime Source

**Status:** IMPLEMENTED (source) — production execution / deployment / credential
issuance / stream creation / candidate-reader provisioning remain **NOT AUTHORIZED**.

## Architecture authority

- ADR-AIEOS-025 — transactional outbox, CloudEvents, JetStream, at-least-once
- ADR-AIEOS-045 — candidate discovery via `integration.list_outbox_dispatch_candidates`
- ADR-AIEOS-046 — production event-plane identity / least privilege

## Governed source SHAs (authorization gate)

| Repository | SHA |
|------------|-----|
| Architecture `origin/main` | `fab7d20da9097b47177afbad66c987b5b5f6c533` |
| Infrastructure `origin/main` | `41c8aac26bf459fab7744efd90bbc595066669b1` |
| Backend base | `36710be8a63636de3b063b44c08819d6c0468137` |

## Scope

**EVENT dispatcher only.** WORKFLOW dispatcher, scheduler/reconciliation, Asset backup,
and production deployment are excluded.

## Executable

```text
python -m aieos.platform.runtime.entrypoints.event_dispatcher_main
```

Importing the module has **zero** external side effects.

## Startup sequence

1. Load EVENT dispatcher runtime config (fail-closed)
2. Configure secret-safe logging
3. Build EVENT SQLAlchemy Engine
4. READ-ONLY database authority probe
5. Parse in-memory NATS `.creds`
6. Connect NATS with verifying TLS + `user_jwt_cb` / `signature_cb`
7. Compose candidate repo, outbox dispatcher, publisher (`expected_stream=AIEOS_EVENTS_PROD`), daemon
8. SIGTERM/SIGINT handling
9. Fair candidate → `dispatch_once(tenant_id)` loop
10. Shutdown: stop new passes, drain NATS, wipe credentials, dispose Engine

## Candidate authority

```sql
SELECT tenant_id, eligible_at
FROM integration.list_outbox_dispatch_candidates(:limit, :as_of)
```

No `security.tenants` scan. No `SELECT DISTINCT tenant_id` queue scan. No candidate table.

## Database role boundary

EVENT dispatcher LOGIN: NOSUPERUSER / NOBYPASSRLS / not schema owner / EXECUTE-only on
candidate function / not a member of candidate-reader.

## In-memory `.creds`

`AIEOS_EVENT_DISPATCHER_NATS_CREDENTIALS` only.  
`AIEOS_EVENT_DISPATCHER_NATS_CREDENTIALS_FILE` is rejected if set.  
No filesystem credential materialization. No `user_credentials=<path>`.

## TLS

STAGING/PRODUCTION require `tls://` with certificate verification (`CERT_REQUIRED`,
`check_hostname=True`). No `ssl.CERT_NONE` / `verify=False`.

## Expected-stream enforcement

Publisher requires PubAck stream == `AIEOS_EVENTS_PROD`. Mismatch → not PUBLISHED →
retry/quarantine (`nats_stream_mismatch`). No stream create/repair. No Core NATS fallback.

## Fairness

Round-robin across candidate tenants with bounded `max_messages_per_tenant_per_pass`.
Candidate batch size 1..1000. Poll interval must be > 0 (no hot poll).

**Operating cadence/batch values are configuration inputs, not a production freeze.**

## Configuration variables

### Shared release

`AIEOS_DEPLOYMENT_ENVIRONMENT`, `AIEOS_RELEASE_VERSION`, `AIEOS_GIT_SHA`,
`AIEOS_BUILD_ID`, `AIEOS_ARTIFACT_DIGEST`

### Secrets (redacted in repr/str)

`AIEOS_EVENT_DISPATCHER_DATABASE_URL`  
`AIEOS_EVENT_DISPATCHER_NATS_CREDENTIALS`

### Non-secret

`AIEOS_EVENT_DISPATCHER_ROLE`  
`AIEOS_EVENT_DISPATCHER_DATABASE_CONNECT_TIMEOUT_SECONDS`  
`AIEOS_EVENT_DISPATCHER_NATS_URL`  
`AIEOS_EVENT_DISPATCHER_NATS_CONNECT_TIMEOUT_SECONDS`  
`AIEOS_EVENT_DISPATCHER_NATS_CA_BUNDLE_PATH` (optional)  
`AIEOS_EVENT_DISPATCHER_POLL_INTERVAL_SECONDS`  
`AIEOS_EVENT_DISPATCHER_CANDIDATE_BATCH_SIZE`  
`AIEOS_EVENT_DISPATCHER_MAX_MESSAGES_PER_TENANT_PER_PASS`  
`AIEOS_EVENT_DISPATCHER_CLAIM_LEASE_SECONDS`  
`AIEOS_EVENT_DISPATCHER_MAX_ATTEMPTS`  
`AIEOS_EVENT_DISPATCHER_RETRY_DELAY_SECONDS`  
`AIEOS_EVENT_DISPATCHER_PUBLISH_TIMEOUT_SECONDS`  
`AIEOS_EVENT_DISPATCHER_SHUTDOWN_GRACE_SECONDS`

## Explicit non-authorizations

Production NATS access/mutation, production credentials/stream, production DB migration,
candidate-reader provisioning, DigitalOcean / OpenTofu / App Platform / Temporal mutation,
production dispatcher execution, WORKFLOW dispatcher, commercial purchase, deployment.

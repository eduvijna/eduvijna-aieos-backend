# PED-I12 — Production WORKFLOW Dispatcher Runtime Source

**Status:** IMPLEMENTED (source) — production execution / deployment / Temporal Cloud
access / credential issuance / candidate-reader provisioning / production DB migration
remain **NOT AUTHORIZED**.

> **PED-I12 Backend source ≠ production WORKFLOW dispatcher activation.**

## Binding architecture

- ADR-AIEOS-026 — workflow implementation baseline
- ADR-AIEOS-029 — production environment deployment readiness baseline
- ADR-AIEOS-031 — production authorization kernel
- ADR-AIEOS-045 — dispatcher tenant candidate discovery authority
- ADR-AIEOS-047 — production workflow-plane identity / least privilege

## Binding Infrastructure contract

`contracts/temporal/production-workflow-plane.yaml` at Infrastructure
`84bd2e6d696af5849c84b9be5cd422b38f14d5ec`

## Governed source SHAs (authorization gate)

| Repository | SHA |
|------------|-----|
| Architecture `origin/main` | `5dec3214ddf170ac7e07096b8eca1d2aad2b9109` |
| Infrastructure `origin/main` | `84bd2e6d696af5849c84b9be5cd422b38f14d5ec` |
| Backend base | `8e837d2ef723db468e18b0405cb8bbc039efa8c2` |

## Classification

**SOURCE ONLY.** This gate implements Backend runtime source, tests, CI wiring, and
documentation for the STAGING/PRODUCTION WORKFLOW_DISPATCHER workload.

It does **not**:

- access Temporal Cloud
- create/mutate Temporal Namespaces, service accounts, or API keys
- execute production Alembic
- provision candidate-reader roles
- mutate DigitalOcean / OpenTofu / App Platform
- deploy workers or dispatchers
- perform production execution

## Executable

```text
python -m aieos.platform.runtime.entrypoints.workflow_dispatcher_main
```

Importing the module has **zero** external side effects.

## Startup sequence (fail-closed)

1. Load WORKFLOW dispatcher runtime config (fail-closed; no `.env`)
2. Configure non-secret logging (`workload=WORKFLOW_DISPATCHER`)
3. Build WORKFLOW dispatcher SQLAlchemy Engine (dispatcher DB URL only)
4. READ-ONLY database authority probe (exact dual `to_regprocedure` OIDs)
5. Connect distinct WORKFLOW_DISPATCHER Temporal client (`tls=True` + dispatcher API key)
6. Outer asyncio timeout bounds **complete** initial `Client.connect`
7. Compose `TemporalClientReviewGateway`
8. Compose existing `ContentReviewStartDispatcher` + `ContentReviewCommandDispatcher`
9. Compose candidate repositories + fair daemon
10. Run until SIGTERM/SIGINT

Configuration/connect/runtime failures → non-zero exit.

## Dispatcher vs worker Temporal credentials

Architecture-frozen WORKFLOW_DISPATCHER Temporal env names (exact):

- `AIEOS_WORKFLOW_DISPATCHER_TEMPORAL_TARGET_HOST`
- `AIEOS_WORKFLOW_DISPATCHER_TEMPORAL_NAMESPACE`
- `AIEOS_WORKFLOW_DISPATCHER_TEMPORAL_API_KEY`
- `AIEOS_WORKFLOW_DISPATCHER_TEMPORAL_CONNECT_TIMEOUT_SECONDS`

Worker family is **not** a dispatcher fallback:

- `AIEOS_TEMPORAL_TARGET_HOST`
- `AIEOS_TEMPORAL_NAMESPACE`
- `AIEOS_TEMPORAL_API_KEY`
- `AIEOS_TEMPORAL_CONNECT_TIMEOUT_SECONDS`

Missing dispatcher Temporal config fails closed. API key is redacted from
`repr` / `str` / logs / exception text. Target host and Namespace may be logged as
non-secret configuration; no production values exist in tests/source.

Common release identity (exact):

- `AIEOS_DEPLOYMENT_ENVIRONMENT`
- `AIEOS_RELEASE_VERSION`
- `AIEOS_GIT_SHA`
- `AIEOS_BUILD_ID`
- `AIEOS_ARTIFACT_DIGEST`

## Database / daemon configuration (source names)

These are implementation/runtime configuration names. They do **not** freeze
production operating values.

- `AIEOS_WORKFLOW_DISPATCHER_DATABASE_URL` (secret; redacted)
- `AIEOS_WORKFLOW_DISPATCHER_ROLE` (lowercase unquoted PostgreSQL identifier; not inferred from URL username alone)
- `AIEOS_WORKFLOW_DISPATCHER_DATABASE_CONNECT_TIMEOUT_SECONDS`
- `AIEOS_WORKFLOW_DISPATCHER_POLL_INTERVAL_SECONDS`
- `AIEOS_WORKFLOW_DISPATCHER_CANDIDATE_BATCH_SIZE` (1..1000)
- `AIEOS_WORKFLOW_DISPATCHER_MAX_INTENTS_PER_TENANT_PER_PASS`
- `AIEOS_WORKFLOW_DISPATCHER_CLAIM_LEASE_SECONDS`
- `AIEOS_WORKFLOW_DISPATCHER_MAX_ATTEMPTS`
- `AIEOS_WORKFLOW_DISPATCHER_RETRY_DELAY_SECONDS`
- `AIEOS_WORKFLOW_DISPATCHER_RESULT_TIMEOUT_SECONDS`
- `AIEOS_WORKFLOW_DISPATCHER_START_RECONCILIATION_TIMEOUT_SECONDS`
- `AIEOS_WORKFLOW_DISPATCHER_SHUTDOWN_GRACE_SECONDS`

Engine: dispatcher URL only; connect timeout applied; no `SET ROLE` / BYPASSRLS /
schema-owner / migrator / deployment-admin / candidate-reader credentials; dispose on cleanup.

## Database authority probe

Exact regprocedures:

```text
workflow.list_start_intent_candidates(integer,timestamp with time zone)
workflow.list_command_intent_candidates(integer,timestamp with time zone)
```

Resolved via `to_regprocedure` OID only (never name+argc / LIKE / first()).

Proves for dispatcher LOGIN:

- `current_user` equals configured role
- LOGIN / NOSUPERUSER / NOBYPASSRLS
- does not own governed AIEOS schemas

Proves for **both** candidate functions:

- exact function exists
- SECURITY DEFINER
- owner NOLOGIN / NOSUPERUSER / NOBYPASSRLS
- dispatcher EXECUTE present
- PUBLIC EXECUTE absent
- both owned by the same workflow candidate-reader authority
- dispatcher is **not** a member of that candidate-reader role
- candidate-reader has no unsafe outbound SUPERUSER/BYPASSRLS/LOGIN memberships (practical check)

Probe is read-only.

## Candidate discovery vs tenant processing

**Candidate discovery** (ADR-AIEOS-045):

```sql
SELECT tenant_id, eligible_at
FROM workflow.list_start_intent_candidates(:limit, :as_of);

SELECT tenant_id, eligible_at
FROM workflow.list_command_intent_candidates(:limit, :as_of);
```

Return shape only: `tenant_id`, `eligible_at`. No payload / command payload /
workflow input / business key / workflow ID. Does **not** set `aieos.tenant_id`,
`SET ROLE`, query `security.tenants`, or mutate.

**Tenant processing** remains `SqlAlchemyWorkflowDispatcherRepository` (sets
transaction-local tenant context before claim/update) + existing start/command
dispatchers (claim / retry / quarantine unchanged).

## Committed-intent semantics

The daemon does **not** query current tenant enabled/suspended status and does **not**
suppress committed START/COMMAND intents when a tenant later becomes SUSPENDED or
DISABLED. Temporal delivery is infrastructure delivery, not business authorization.
Sensitive effects continue to revalidate current business authority at Activity /
application-command boundaries.

## Operation fence

Before any Temporal start call, `task_queue` must be **exactly** `aieos.content.review`.
Blank or arbitrary queues fail closed **before** Temporal is called (no silent
normalization).

Governed start:

- workflow type: `ContentReviewWorkflowV1`
- task queue: `aieos.content.review`
- signal: `review_decision_recorded`

No terminate / cancel / reset / batch / Schedule / Search Attribute / Nexus /
Namespace / Cloud Ops / service-account / API-key / worker-polling methods are
exposed by the dispatcher gateway.

Existing delivery reconciliation in `TemporalClientReviewGateway` is preserved
(START REJECT_DUPLICATE / history identity; COMMAND describe / terminal /
signal / result match). PED-I12 does **not** expand into a general scheduled
runtime / repair daemon / Temporal Schedule subsystem.

## Fairness model

Each pass:

1. Discover START candidates (bounded batch)
2. Discover COMMAND candidates (bounded batch)
3. Fair round-robin per stream with `max_intents_per_tenant_per_pass`
4. Tenants that return no work drop from later rounds (no noisy-tenant starvation)
5. Stream order alternates each pass (`START` then `COMMAND`, then reverse) so
   one stream cannot indefinitely suppress the other
6. Poll wait (`poll_interval_seconds > 0`) prevents hot spin after failures

`claimed_by` shape: `aieos.workflow-dispatcher/<build_id>/<uuid>`

## Shutdown model

SIGTERM / SIGINT:

- stop new passes
- allow current in-flight pass under `AIEOS_WORKFLOW_DISPATCHER_SHUTDOWN_GRACE_SECONDS`
- cancel daemon task if grace expires
- dispose Engine

Shutdown grace covers in-flight daemon work, not merely transport cleanup.

## Observability boundary

Log non-secret operational evidence only (workload, environment, git SHA,
claimed_by, candidate kind START/COMMAND, counts, tenant_id, delivery outcome
category, attempt/quarantine signals when already exposed). Never log Temporal
API key, DB URL/password, workflow start/command payload, or authorization tokens.

## Provider RBAC coarseness / no live Temporal Cloud proof

Local/mock tests prove Backend protocol behavior only. They do **not** prove
Temporal Cloud Account READ, Namespace WRITE, service-account isolation,
Cloud Ops denial, or Custom Roles behavior. Those remain later
provisioning/conformance gates.

## Production stop gate

| Action | Authorized by PED-I12? |
|--------|------------------------|
| Backend source/tests/CI/docs | YES |
| Production Temporal Cloud access | NO |
| Production Temporal Namespace mutation | NO |
| Temporal service-account / API-key mutation | NO |
| Production DB access / Alembic | NO |
| Candidate-reader provisioning | NO |
| DigitalOcean / OpenTofu / App Platform | NO |
| Worker / dispatcher deployment | NO |
| Production execution | NO |
| Commercial purchase | NO |

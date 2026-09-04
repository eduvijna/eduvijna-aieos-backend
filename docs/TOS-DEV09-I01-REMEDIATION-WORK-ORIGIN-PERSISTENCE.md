# TOS-DEV09-I01 — Remediation TeachingWork + Immutable Origin Domain/Persistence

Adds the Teaching-domain substrate for ADR-AIEOS-056 OPTION B:
`TeachingWork(intent_type=remediate_class)` plus immutable
`TeachingWorkRemediationOrigin`. No Improve HTTP command, Assessment
eligibility composition, ClassRef gate, audit, UX, generation, or Product E2E
(DEV09-I02+).

## Frozen architecture authority

- ADR-AIEOS-056 — AIEOS Improve & Remediation Authority
- Status: Frozen / Approved v1.0.2
- Architecture pin: `ed57b338d5b527475e1a869097c7ac273f1060a1`
- Backend authorized base: `1fe28f4fd1a2a2070aa69d67daa49cd53ba5820d`
- Frontend read-only baseline: `30c94f3e0403b9a5a2e955c706766035490598f9`
- Product read-only baseline: `9faef1406b4b2906e2199267379ee0e709f75462`

## Constitutional position

| Concept | Status in this slice |
| --- | --- |
| Improve capability | Teaching application capability (OPTION B) — not a separate SoR |
| IntentType `remediate_class` | Added; Teaching Intent remains transient (no `teaching_intents` table) |
| TeachingWork | Durable remediation preparation container |
| TeachingWorkRemediationOrigin | Immutable Teaching-owned 1:1 provenance |
| Generic `POST /api/v1/teaching/works` | Continues to reject `remediate_class` until I02 |
| Assessment eligibility / ClassRef / audit | Deferred to DEV09-I02 |
| Improve UX / generation / Product E2E | Deferred |
| Learner / Mastery / Teacher Memory | **Not** introduced |
| Improve NATS / Temporal | **Not** introduced |

## Intent type

Exact values:

- `prepare_tomorrow`
- `remediate_class`

Domain enum and `ck_teaching_works_intent_type` widened together.

## Origin fields

Immutable fields only:

`work_id`, `tenant_id`, `source_assessment_id`,
`source_assessment_aggregate_revision`, `source_class_result_level_snapshot`,
`source_class_ref`, `source_content_id`, `source_content_version_id`,
`source_work_id` (nullable), `source_execution_id` (nullable),
`source_assignment_id` (nullable), `initiating_teacher_principal_id`,
`created_at`.

Snapshot vocabulary (Teaching-owned provenance): `DEMONSTRATED` / `MIXED` /
`NOT_YET_DEMONSTRATED`.

No `updated_at`, metadata bag, `class_result_note`, CLASS_OBSERVATION body,
PRIVATE_EXECUTION_NOTE, or note/observation inclusion flags.

## Immutability

- Python repository: insert + get by `work_id` only
- PostgreSQL: BEFORE UPDATE/DELETE triggers reject mutation
- Assessment CORRECT/VOID cannot mutate this table

## RLS

`ENABLE` + `FORCE ROW LEVEL SECURITY` on `teaching.work_remediation_origins`
using existing `teaching.current_tenant_id()`.

## Generic-create remediation rejection

`CreateTeachingWorkService` rejects `intent_type=remediate_class` before Work
insert and before any idempotency outcome is committed. Dedicated Assessment-
origin create remains DEV09-I02.

## Migration

- Alembic: `tosd090001` (down_revision `tosd080002`) — **new Backend head**
- Empty downgrade to `tosd080002` succeeds
- Non-empty downgrade refuses when any `remediate_class` Work or origin exists
- DEFERRABLE commit-time CONSTRAINT TRIGGERs enforce Work↔origin pair integrity

## I02 deferred responsibilities

Assessment RECORDED eligibility, `expected_assessment_aggregate_revision`,
teacher ownership composition, School Context ClassRef authorization, Improve
audit vocabulary, Improve idempotency operation, HTTP
`POST /api/v1/teaching/works/from-classroom-assessment`, Improve UX, generation
AI-input boundary enforcement at application layer, Product E2E.

## Explicit non-goals

Improve HTTP route, OpenAPI change, Frontend `/teacher-os/improve`, automatic
recommendations, Teacher Memory, learner identity/groups, Mastery, NATS Improve
events, Temporal Improve workflow, production deployment, DigitalOcean mutation.

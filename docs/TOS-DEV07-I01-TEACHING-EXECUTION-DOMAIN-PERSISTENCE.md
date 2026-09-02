# TOS-DEV07-I01 — TeachingExecution Domain + PostgreSQL Persistence / RLS

Adds the durable **TeachingExecution** Teaching-domain System of Record for
actual classroom teaching, plus exact ContentVersion bindings and permitted
observations. No HTTP/API, no application orchestration, no events/audit, and
no ClassRef current-authority gate (DEV07-I02).

## Frozen architecture authority

- ADR-AIEOS-054 — AIEOS Teaching Execution & Observation Authority
- Status: Frozen / Approved v1.0.0
- Architecture main: `0f9c91229fc6610667517f8e1776fe6e9e7b2d43`
- Backend authorized base: `06e05277e73e0c71172cae4904efb37d771c3fad`

## Constitutional position

| Concept | Status in this slice |
| --- | --- |
| TeachingExecution | Durable Teaching-domain SoR for classroom execution |
| TeachingExecutionContentBinding | Exact immutable ContentVersion identity used during execution |
| TeachingExecutionObservation | `PRIVATE_EXECUTION_NOTE` / `CLASS_OBSERVATION` only |
| PreparationKit | **Not** introduced |
| Class / Roster / Enrollment / timetable | External authority — **not** Teaching tables |
| ClassRef | Opaque School Context identifier; no FK; I02 validates current authority |
| teacher_principal_id | Represented / effective **HUMAN** teacher — not HTTP/service principal |
| Events / audit / Idempotency-Key START | Deferred to I02 |
| Teach HTTP / OpenAPI / Frontend | Deferred to later DEV07 slices |

## Aggregate semantics

Lifecycle: `IN_PROGRESS` → `COMPLETED` | `CANCELLED` (terminal).

`COMPLETED` means only that the represented teacher recorded classroom execution
as completed. It does **not** imply assignment closed, external delivery,
learner attempt, submission, assessment, grade, or mastery.

Rejected lifecycle states: PLANNED, SCHEDULED, DELIVERED, ASSESSED, GRADED,
MASTERED. Assigned remains TeachingAssignment authority.

## Persistence

- Tables: `teaching.executions`, `teaching.execution_content_bindings`,
  `teaching.execution_observations`
- Alembic: `tosd070001` (down_revision `tosd060002`) — **new Backend head**
- Required work FK to `teaching.works (tenant_id, work_id)`
- Binding composite FK to `content.content_versions (tenant_id, content_id, version_id)`
- Tenant RLS via existing `teaching.current_tenant_id()` / `aieos.tenant_id`
- Optimistic concurrency: execution `aggregate_revision`; observation `revision`
- Observation mutation locks parent execution and fails closed when terminal
- **No** business uniqueness over teacher/work/class/date

## Explicit non-goals

No Teach API, Teach UX, START Idempotency-Key application service, ClassRef
authority call, events/NATS, security-audit emission, PreparationKit, learner
observations, OpenAPI change, Frontend/Product/Architecture mutation, or
DEV07-I02+.

# TOS-DEV08-I01 — ClassroomAssessment Domain + PostgreSQL Persistence / RLS

Adds the durable **ClassroomAssessment** Assessment-domain System of Record for
class-level assessment evidence. No HTTP/API, no application orchestration, no
events/audit/idempotency, and no ClassRef or ContentVersion Cases A/B/C gates
(DEV08-I02).

## Frozen architecture authority

- ADR-AIEOS-055 — AIEOS Assessment & Learning Evidence Authority
- Status: Frozen / Approved v1.0.2
- Architecture pin: `cabb26276ac14c5531dbaac5c759e749b48ea54d`
- Backend authorized base: `551e46e004233421746e4df2789c07367702528b`

## Constitutional position

| Concept | Status in this slice |
| --- | --- |
| ClassroomAssessment | Durable Assessment-domain SoR for class-level evidence |
| ClassRef | Opaque School Context identifier; no FK; I02 validates current authority |
| ContentVersion | Opaque composition identity; no FK; I02 Cases A/B/C |
| TeachingWork / TeachingExecution / TeachingAssignment | Optional opaque composition identities; no cross-domain ownership |
| learner / student / roster / attempt / submission | **Not** introduced |
| Mastery / Improve | **Not** introduced |
| Events / audit / Idempotency-Key | Deferred to I02 |
| Assess HTTP / OpenAPI / Frontend | Deferred; `/teacher-os/assess` remains placeholder |

## Aggregate semantics

Lifecycle: `RECORD` → `RECORDED`; `CORRECT` stays `RECORDED`; `VOID` → `VOIDED`
(terminal). No DELETE.

Class result: `DEMONSTRATED` / `MIXED` / `NOT_YET_DEMONSTRATED` only.

`RECORDED` means only that the represented teacher recorded this class-level
judgement. It does **not** imply mastery, learner attempt, or Improve.

## Persistence

- Schema: `assessment` (application/content schema-owner posture)
- Table: `assessment.classroom_assessments`
- Alembic: `tosd080001` (down_revision `tosd070002`) — **new Backend head**
- Tenant RLS via `assessment.current_tenant_id()` / `aieos.tenant_id`
- Optimistic concurrency: `aggregate_revision` compare-and-set
- **No** cross-domain PostgreSQL foreign keys
- **No** business uniqueness over teacher/class/content/date
- Empty downgrade to `tosd070002` succeeds; non-empty downgrade refuses

## Explicit non-goals

DEV08-I02 application services, Assessment REST/OpenAPI, School Context current
authority, ContentVersion Cases A/B/C, Teaching composition validation,
capability identifiers, security audit vocabulary, Idempotency-Key, Teacher OS
Assess UI, Improve, learner-specific Assessment, Student OS, Mastery, events,
NATS, Temporal, AI grading, production deployment.

# TOS-DEV08-I02 — ClassroomAssessment Application/API + Authority Composition

Makes the durable ClassroomAssessment SoR usable through governed commands and
reads. Domain/persistence (DEV08-I01) remains unchanged in contract.

## Frozen architecture authority

- ADR-AIEOS-055 — Frozen / Approved
- Architecture pin: `cabb26276ac14c5531dbaac5c759e749b48ea54d`
- Backend base: `79ebb7475979b35fa775082757f06b7ec54d538e`
- I01: formally closed

## HTTP surface

| Method | Path | operation_id |
| --- | --- | --- |
| POST | `/api/v1/assessment/classroom-assessments` | `assessment_classroom_record` |
| GET | `/api/v1/assessment/classroom-assessments` | `assessment_classroom_list` |
| GET | `/api/v1/assessment/classroom-assessments/{assessment_id}` | `assessment_classroom_get` |
| POST | `.../actions/correct` | `assessment_classroom_correct` |
| POST | `.../actions/void` | `assessment_classroom_void` |

Trusted identity only. Body never accepts `tenant_id` / `teacher_principal_id`.
Mutations require `Idempotency-Key`. CORRECT/VOID require `If-Match`. Strong ETag
uses existing `r{n}` revision helpers.

## ClassRef mutation authority

Bootstrap current-class authority reuses School Context
`require_assignable_class_ref`. This is **not** a permanent claim that
assignable == assessable. Historical GET/LIST do **not** require current ClassRef.

## Cases A / B / C

- **A** `execution_id`: COMPLETED TeachingExecution; exact binding; eligible
  artifact kinds only; do not follow current published pointer.
- **B** `assignment_id`: CLASS audience; exact immutable content binding.
- **C** neither: race-safe `published_version_id` match; quiz/worksheet/homework.
- Both A+B must independently pass and mutually agree.
- Optional `work_id` is additional composition validation.

## Idempotency / audit

- `assessment_classroom_record.v1` / `correct.v1` / `void.v1`
- Audit actions: `assessment.classroom.record|correct|void`
- Alembic: `tosd080002` (down_revision `tosd080001`); nonempty Assessment audit
  evidence refuses downgrade.

## Authorization vocabulary

Capability strings `assessment.classroom.record|correct|void|read|list` are
documented and aligned with audit names. HTTP composition follows Teaching:
trusted identity + ClassRef + ownership — not a parallel AuthorizationKernel
grant path.

## Explicit non-goals

Teacher OS Assess UI, Improve, learner Assessment, Mastery, AI grading, NATS
events, Temporal, production deployment.

## OpenAPI

New canonical digest (I02):

`824B389D6D4EDB2EA5D8ED3A9E5411087B566DFDCA09C2AB0CD4FDED51C4D89D`

Frontend remains untouched.

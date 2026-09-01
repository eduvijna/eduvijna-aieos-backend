# TOS-DEV06-I03 — TeachingAssignment Application / API / Events / Audit

Adds authoritative TeachingAssignment command services, HTTP API, transactional
outbox events, and security audit evidence on Alembic head `tosd060002`.

## Scope

- CREATE with dual gates: School Context ClassRef current authority (fresh path only)
  and race-safe published learner Content eligibility (inside txn)
- Due update via `PATCH /api/v1/teaching/assignments/{assignment_id}` (due_at only),
  close, and cancel mutations with If-Match + Idempotency-Key
- TeachingAssignment list/get for owning teacher; responses include `teacher_principal_id`
- Transactional outbox: `io.eduvijna.aieos.teaching.assignment.*.v1`
- Security audit: `teaching.assignment.create|due_update|close|cancel`
- Historical `tosd060001` unchanged; forward `tosd060002` extends audit CHECK constraints

## Explicit non-goals

No Frontend/Architecture/Infrastructure/Product changes, no Class/Roster SoR,
no LMS delivery.

## I03R1 corrections

- Restored immutable historical `tosd060001`
- Forward migration `tosd060002` for Teaching audit constraints only
- Idempotent CREATE replay skips ClassRef/Content revalidation
- PostgreSQL publication race CASE A + CASE B
- Real outbox/audit/idempotency failure rollback tests

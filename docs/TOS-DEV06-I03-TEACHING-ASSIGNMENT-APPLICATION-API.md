# TOS-DEV06-I03 — TeachingAssignment Application / API / Events / Audit

Adds authoritative TeachingAssignment command services, HTTP API, transactional
outbox events, and security audit evidence on amended head `tosd060001`.

## Scope

- CREATE with dual gates: School Context ClassRef current authority (before txn)
  and race-safe published learner Content eligibility (inside txn)
- Due update, close, and cancel mutations with If-Match + Idempotency-Key
- TeachingAssignment list/get for owning teacher
- Transactional outbox: `io.eduvijna.aieos.teaching.assignment.*.v1`
- Security audit: `teaching.assignment.create|due_update|close|cancel`
- Amended `tosd060001` extends `security.audit_records` CHECK constraints

## Explicit non-goals

No Frontend/Architecture/Infrastructure/Product changes, no new Alembic head,
no Class/Roster SoR, no LMS delivery.

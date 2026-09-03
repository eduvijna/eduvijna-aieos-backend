# TOS-DEV07-I02 — Teach Composition Application / API

Adds TeachingExecution command services, observation create/correct, Teach
composition read, HTTP API, transactional outbox events, and security audit on
Alembic head `tosd070002`.

## Scope

- `StartTeachingExecutionService` with ClassRef current-authority gate (fresh
  path) and optional Work artifact binding validation; zero bindings accepted
- Observation create/correct (`PRIVATE_EXECUTION_NOTE` / `CLASS_OBSERVATION`
  only); no observation outbox events
- Complete / cancel with If-Match + Idempotency-Key; ClassRef revalidated on
  every mutation
- `GetTeacherOsTeachContextService` — projection only (Work + ClassRef +
  artifacts + related assignments/executions)
- HTTP under `/api/v1/teaching/executions` and
  `GET /api/v1/teacher-os/teach/context`
- Transactional outbox: `io.eduvijna.aieos.teaching.execution.{started,completed,cancelled}.v1`
- Security audit: `teaching.execution.start|complete|cancel` and
  `teaching.execution.observation.create|correct`
- Idempotency for start / complete / cancel / observation create / correct
- Aggregate revision (execution) and observation revision concurrency

## ClassRef gate

School Context ClassRef is revalidated on start and on every subsequent
mutation (complete, cancel, observation create/correct). Unavailable School
Context fails closed. Revocation after START denies later mutations.

## Explicit non-goals

No Frontend / Architecture / Infrastructure / Product changes, no Class/Roster
SoR, no learner-specific observation kinds, no observation CloudEvents, no
assignment lifecycle coupling, no new Alembic revision beyond audit vocabulary
`tosd070002`.

Alembic head: `tosd070002`.

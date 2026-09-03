# TOS-DEV07-I02R1 — TeachingExecution Security Audit Vocabulary

Extends the immutable `security.audit_records` CHECK vocabulary so TOS-DEV07-I02
can emit TeachingExecution mutation audit without violating PostgreSQL
constraints. No TeachingExecution application/API/HTTP/OpenAPI work.

## Frozen architecture authority

- ADR-AIEOS-054 — AIEOS Teaching Execution & Observation Authority
- Backend authorized base: `79eff7ff120e7dcb49735442ca4488fdfac89841`
- Architecture main: `0f9c91229fc6610667517f8e1776fe6e9e7b2d43`

## Scope

- Alembic `tosd070002` (down_revision `tosd070001`) — audit CHECK extension only
- Actions:
  - `teaching.execution.start` (create: NULL → 0)
  - `teaching.execution.complete` / `cancel` (increment: n → n+1)
  - `teaching.execution.observation.create` (create: NULL → 0)
  - `teaching.execution.observation.correct` (increment: n → n+1)
- Python `SecurityAuditAction` families + SQLAlchemy mapping mirror the DB
- Downgrade fails closed only when TeachingExecution audit evidence exists
- TeachingAssignment-only evidence does **not** block downgrade to `tosd070001`

## Explicit non-goals

No TeachingExecution HTTP/API, Teach composition, observation/application
services, outbox events, School Context changes, OpenAPI change, Frontend,
DEV07-I02 application implementation, or DEV07-I03.

Alembic head: `tosd070002`. OpenAPI digest unchanged.

# TOS-DEV06-I02 — TeachingAssignment Domain + PostgreSQL Persistence / RLS

Adds the durable **TeachingAssignment** Teaching-domain System of Record for
teacher-owned classroom assignment intent. No HTTP/API, no application command
service, and no CREATE publication/ClassRef authority execution (DEV06-I03).

## Constitutional position

| Concept | Status in this slice |
| --- | --- |
| TeachingAssignment | Durable Teaching-domain SoR for classroom assignment intent |
| Publication | Unchanged; Published ≠ Assigned |
| Class / Roster / Enrollment | External Admin / ERP / SIS authority — **not** Teaching tables |
| ClassRef | Opaque string School Context identifier; no FK |
| TeachingWork.class_label | Presentation text only — **not** Class identity |
| CREATE gates | Deferred to I03 (publication eligibility + ClassRef revalidation) |
| Events / audit / Idempotency-Key | Deferred to I03 |

## Lifecycle

`ACTIVE` → `CLOSED` | `CANCELLED` (terminal). No DRAFT / SCHEDULED / reopen.
Due-date passage does not auto-close.

## Persistence

- Table: `teaching.assignments`
- Alembic: `tosd060001` (down_revision `tosd040001`)
- Composite FK to `content.content_versions (tenant_id, content_id, version_id)`
- Optional composite FK to `teaching.works (tenant_id, work_id)`
- Tenant RLS via existing `teaching.current_tenant_id()` / `aieos.tenant_id`
- **No** business uniqueness over teacher/content/class/due

## Explicit non-goals

No assignment API, Assign UI, LMS, Class/Roster SoR, migration beyond
`tosd060001`, OpenAPI change, Frontend/Product/Architecture change, or
deployment.

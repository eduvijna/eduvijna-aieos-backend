# TOS-DEV06-I01 — School Context ClassRef Read Contract

Adds the narrow **School Context ClassRef read** boundary required before
TeachingAssignment. No TeachingAssignment aggregate, persistence, or Assign UI
is authorized in this slice.

## Constitutional position

| Concept | Status in this slice | Where it lives |
| --- | --- | --- |
| Class / Roster / Enrollment master data | External authority (Admin / ERP / SIS) | School Context provider (not Teaching DB) |
| ClassRef | Opaque School Context identifier | `AssignableClassRef.class_ref` |
| TeachingWork.class_label | Contextual presentation text only | Unchanged; **not** Class identity |
| School Context GET/list | Current-authority UX assistance | `GET /api/v1/teacher-os/school-context/classes` |
| TeachingAssignment CREATE | **Not implemented** | Future DEV06 slice; must revalidate ClassRef |

Canonical call path:

```text
Browser → AIEOS → School Context port → future ERP / SIS / Admin provider
```

Not:

```text
Browser → ERP / SIS directly
Teaching database → new Class / Roster SoR
```

## Bounded context layout

```
src/aieos/domains/teaching/application/school_context.py
  AssignableClassRef, SchoolContextClassReader, ListAssignableSchoolClassesService

src/aieos/development/school_context.py
  DevelopmentSchoolContextClassReader  (NON_PRODUCTION only)

src/aieos/domains/teaching/api/v1/
  models / dependencies / routes — HTTP surface
```

## HTTP contract

| Method | Path | Operation ID |
| --- | --- | --- |
| GET | `/api/v1/teacher-os/school-context/classes` | `teacher_os_school_context_classes_list` |

Trusted `tenant_id` and `principal_id` come only from `resolve_trusted_context`.
No `Idempotency-Key` / `If-Match` (read of current authority, not mutable aggregate).

Response shape:

```json
{
  "items": [
    {"class_ref": "class-5a", "display_label": "Grade 5A"}
  ]
}
```

Semantics:

- `200` + `items=[]` — teacher currently has no assignable classes (valid)
- `503` `school_context_unavailable` — provider unavailable / not composed / contract failure
- List result is **not** durable authorization for future TeachingAssignment CREATE

## Composition

- Production `compose_api_application` does **not** inject a School Context reader → endpoint fails closed `503`
- Teacher OS development factory injects `DevelopmentSchoolContextClassReader` only
- Development adapter is NON_PRODUCTION, synthetic, offline, and must not be imported by production runtime

## Explicit non-goals (I01)

No TeachingAssignment, Class/Roster SoR, migration, OpenAPI mutation semantics,
ERP/SIS vendor selection, LMS connector, frontend Class picker, or deployment.
Alembic remains `tosd040001`.

# TOS-DEV02 Lane B — Teaching Work and Today's Mission

Adds the durable **Teaching Work** aggregate and the derived **Today's Mission**
projection. No AI generation, no agents, no MCP surface, and no Teaching Intent
System of Record.

## Constitutional position

| Concept | Status in this slice | Where it lives |
| --- | --- | --- |
| Teaching Intent | Request that enters Work creation. Never persisted as its own aggregate. | `IntentType` value object + `CreateTeachingWorkCommand` |
| Teaching Work | Durable teacher-owned preparation container. | `teaching.works` in PostgreSQL |
| Today's Mission | Derived read projection, recomputed on every request. | `GetTeacherOsTodayMissionService` |

Deleting every Mission code path would lose no durable state; deleting Teaching
Work would lose the teacher's preparation. That asymmetry is the test the
adversarial suite enforces.

## Bounded context layout

```
src/aieos/domains/teaching/
  domain/          identities, intent_type, work aggregate, errors
  application/     errors, models, ports, create, refine, queries,
                   mission_models, mission, review_queue_port
  infrastructure/  metadata, models, repositories, uow, errors
  api/v1/          dependencies, models, routes
```

The domain layer imports no SQLAlchemy, FastAPI, or infrastructure module; the
application layer imports neither SQLAlchemy nor FastAPI. Both rules are
enforced by `tests/domains/teaching/test_tos_dev02_architecture.py`.

## HTTP contract (`/api/v1`)

| Method | Path | Operation ID | Preconditions |
| --- | --- | --- | --- |
| POST | `/teaching/works` | `teaching_work_create` | `Idempotency-Key` required. Returns 201 with `ETag` and `Location`. |
| GET | `/teaching/works` | `teaching_work_list` | Teacher-scoped. `include_archived` defaults to false. |
| GET | `/teaching/works/{work_id}` | `teaching_work_get` | Returns `ETag`. |
| PATCH | `/teaching/works/{work_id}` | `teaching_work_refine` | `If-Match` and `Idempotency-Key` required. |
| GET | `/teacher-os/today/mission` | `teacher_os_today_mission` | `mission_date` query parameter required. |

Refinable fields are `goal_text`, `class_label`, `subject`, `topic`,
`target_date`, and `locale`. PATCH uses true partial semantics: a field absent
from the JSON body is left untouched, while an explicit `null` clears a
nullable field. `goal_text`, `target_date`, and `locale` are non-nullable, so an
explicit `null` for them is rejected with `invalid_teaching_work_request`.

Identity, ownership, `intent_type`, and `created_at` are immutable after
creation. Every read and mutation is filtered by `teacher_principal_id`: another
teacher inside the same tenant receives 403, and another tenant receives 404
because Row-Level Security hides the row entirely.

### Mission date is a temporary DEV02 contract

`mission_date` is supplied by the client as a validated calendar date because no
teacher time-zone System of Record exists yet. Once teacher time zones are
governed, the server derives the local educational day and this query parameter
is removed. The service treats the value as opaque input and echoes it back in
the projection.

## Mission composition

The projection is assembled from two sources on every read:

1. `ReviewQueuePendingCountPort` — the pending Review Queue count. The adapter
   walks the published `ListTeacherReviewQueueService` projection in pages of
   100, bounded to 50 pages, so a large queue can never turn a Mission read into
   an unbounded scan. Teaching never reads Content tables directly.
2. The teacher's active, non-archived Teaching Work — a count plus the most
   recently updated Work as the `continue_work` candidate.

Hero action priority:

| Condition | `hero_action.kind` |
| --- | --- |
| pending review count > 0 | `review` |
| otherwise, an active Work exists | `continue_work` (carries `work_id`) |
| otherwise | `prepare_tomorrow` |

## Persistence

Migration `tosd020001` (`down_revision: adra045001`) creates the `teaching`
schema and one table, `teaching.works`:

- UUIDv7 `work_id` primary key, plus `uq_teaching_works_tenant_work`.
- `tenant_id`, `teacher_principal_id`, `intent_type`, `goal_text`,
  `class_label`, `subject`, `topic`, `target_date DATE`, `locale`,
  `aggregate_revision`, `created_at`, `updated_at`, `archived_at`.
- CHECK constraints for a non-negative revision, non-empty text fields, and
  `updated_at >= created_at`.
- `ck_teaching_works_intent_type` currently allows `prepare_tomorrow` only.
  Widening this CHECK is the required migration step whenever `IntentType`
  gains a member.
- Indexes on `(tenant_id)`, `(tenant_id, teacher_principal_id)`,
  `(tenant_id, teacher_principal_id, target_date)`, and
  `(tenant_id, archived_at)`.
- `teaching.current_tenant_id()` plus `ENABLE` and `FORCE ROW LEVEL SECURITY`
  with a tenant isolation policy, mirroring the Content schema.

`downgrade()` drops only `SCHEMA teaching CASCADE`. The migration touches no
other domain schema.

**`class_label` is contextual free text** captured from the teacher, for example
`"Grade 5B"`. It is not a foreign key into any Class System of Record and must
never be treated as one. There is no `ForeignKey` anywhere in the teaching
persistence models.

The runtime role is granted `SELECT, INSERT, UPDATE` on `teaching.works` and
`EXECUTE` on `teaching.current_tenant_id()`; `DELETE` is explicitly revoked.

### Expected head moved

`EXPECTED_ALEMBIC_HEAD` (`src/aieos/platform/runtime/readiness.py`) and
`EXPECTED_MIGRATION_HEAD` (`tools/release/common.py`) move from `adra045001` to
`tosd020001`. `teaching` is added to `_CONTENT_OWNED_SCHEMAS` and to the owner
check SQL, because migrations create it under the content schema owner role.

## Idempotency

Both mutations reuse the existing platform idempotency records in
`api.idempotency_records` through `SqlAlchemyIdempotencyRepository`:

- `TEACHING_WORK_CREATE_V1 = "teaching_work_create.v1"`
- `TEACHING_WORK_REFINE_V1 = "teaching_work_refine.v1"`

The stored outcome column is named `result_content_id` because the platform
table predates this bounded context. **It is a generic result UUID**, and
Teaching stores the `work_id` in it. No Content row is implied.

Replaying a key with the same request returns the recorded result. Reusing a key
with a drifted request fails with `idempotency_key_reused` (409).

## Development reference data

`src/aieos/development/teacher_os_teaching_work_scenario.py` seeds two synthetic
Teaching Work rows through the published HTTP contract and then reads the
Mission. It reuses the TOS-DEV01 synthetic tenant and principal so both
scenarios describe one coherent synthetic teacher. `target_date` is tomorrow
relative to the supplied scenario date.

The scenario is idempotent twice over: it first lists existing Work and reuses
any row carrying the scenario `goal_text` marker, and it uses deterministic
`Idempotency-Key` values inside the retention window.

Run it explicitly — it never executes on application, migration, or worker
startup:

```bash
uv run python tools/development/load_teacher_os_teaching_work_scenario.py \
  --database-url postgresql+psycopg://aieos_runtime:...@127.0.0.1:55432/aieos
```

## Tests

| File | Covers |
| --- | --- |
| `tests/domains/teaching/test_tos_dev02_teaching_work_postgres.py` | create, refine, stale `If-Match` 412, tenant RLS isolation, teacher ownership, durability across Unit of Work recreation, revoked DELETE, and `information_schema` proof that no intent or mission table exists |
| `tests/domains/teaching/test_tos_dev02_mission_postgres.py` | hero scenarios A/B/C against a real Review Queue and real Work rows, teacher scoping, tenant isolation, `mission_date` validation, and projection purity |
| `tests/domains/teaching/test_tos_dev02_architecture.py` | adversarial guards: no intent table in any migration, no mission table, exactly one teaching table, mission service never writes, layering rules, OpenAPI operations, and no generation surface |
| `tests/development/test_tos_dev02_teaching_work_scenario.py` | loader seeds, re-runs idempotently, writes a non-secret report, and creates no intent or mission rows |

All of them run against real PostgreSQL 18 via the `runtime_engine` fixture.
Marker: `tos_dev02`.

```bash
uv run pytest tests/domains/teaching/ \
  tests/development/test_tos_dev02_teaching_work_scenario.py -q
```

## Explicitly out of scope

- AI generation, agents, and MCP.
- A `teaching_intents` table or any durable Teaching Intent aggregate.
- A mission table, mission cache, or mission write path.
- Archiving over HTTP: `archived_at` exists in the schema and is honoured by the
  read path, but no TOS-DEV02 endpoint sets it.
- Changes to the Architecture, Product, or Infrastructure repositories.

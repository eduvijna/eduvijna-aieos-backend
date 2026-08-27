# TOS-DEV01 Lane B — Development Reference Review Scenario

**Status:** NON_PRODUCTION  
**Slice:** TOS-DEV01 Lane B  
**Base Backend SHA:** `bcfd5eb054ef07c30219cfae0ca9ccd7279ea8c0`

## Purpose

First piece of the AIEOS Development Reference Data System for the Teacher OS
Review Queue product spine. Seeds synthetic Generic Content artifacts through
existing application HTTP contracts so the Review Queue list/detail and review
actions can be exercised against real PostgreSQL state.

## Non-goals

- No production catalog/schema expansion
- No Alembic migration
- No production auth bypass
- No automatic run on app startup, migration, worker, or deploy

## Content type used

Production Content catalog remains empty/fail-closed.

DEV01 reuses the existing development/test fixture type:

| Field | Value |
|-------|--------|
| content_type | `test.generic` |
| schema_id | `test.generic` |
| schema_version | `1` |

Educational titles are synthetic labels only (worksheet / lesson outline /
quick-check wording). They do not introduce new Content type architecture.

## Scenario artifacts

Idempotent keys under scenario id `tos-dev01-teacher-os-review-queue`:

1. **approve-demo** — suitable for APPROVE
2. **request-changes-demo** — suitable for REQUEST_CHANGES
3. **reject-demo** — suitable for REJECT

Path: create → append version → submit-for-review → Review Queue visibility.

## Explicit load command

```bash
uv run python tools/development/load_teacher_os_review_scenario.py \
  --database-url "postgresql+psycopg://<runtime-role>@127.0.0.1:55432/<db>"
```

Writes non-secret evidence to `tmp/tos-dev01-review-scenario.json` (gitignored).

Repeatability: deterministic Idempotency-Key values per `(tenant, artifact, step)`
and queue title matching prevent unbounded duplicates when re-run against the
same synthetic tenant before decisions are consumed.

## Tests

```bash
uv run pytest -v tests/development/test_tos_dev01_review_spine.py
```

Proves PostgreSQL-backed APPROVE / REQUEST_CHANGES / REJECT spines plus ETag,
If-Match, Idempotency-Key, 412, and tenant isolation on real Review Queue HTTP
routes.

## Production / security

- Production runtime composition unchanged
- Production JWT trust model unchanged
- RLS / tenant authority unchanged
- OpenAPI contract unchanged (no route semantics change)

# AIEOS Local Development

LOCAL DEVELOPMENT ONLY — credentials and adapters documented here must never be used in production.

## FOUNDER QUICK START

1. Start Docker Desktop.
2. Open `eduvijna-aieos-backend` in Cursor.
3. Go to **Run and Debug**.
4. Select **AIEOS API — Local Development**.
5. Press **F5**.
6. Open http://127.0.0.1:8080/docs
7. Open http://127.0.0.1:8080/readyz
8. Connect DB using:
   - Host `127.0.0.1`
   - Port `55432`
   - DB `aieos`
   - User `aieos_bootstrap`
   - Password `aieos_test`

### How to Stop API

Stop the debugger session in Cursor (Shift+F5 or the Stop button).

### How to Stop DB

Run task **AIEOS: Local DB Stop** or:

```powershell
uv run python tools/dev/local_db.py stop
```

This stops the container but keeps the `aieos-local-postgres-data` volume.

### How to Reset DB

Run task **AIEOS: Local DB Reset** or:

```powershell
uv run python tools/dev/local_db.py reset --confirm
```

This destroys the container and named volume. All local data is removed.

### How to Restart F5 Without Losing Data

Press F5 again. The preLaunch task reuses the running or stopped container and volume; migrations are applied idempotently. Data persists between API restarts unless you run **Reset**.

---

## F5 Workflow

When you press F5 with **AIEOS API — Local Development**:

1. Verifies Docker is reachable.
2. Starts or reuses container `aieos-local-postgres` (PostgreSQL 18).
3. Provisions local roles idempotently (`aieos_bootstrap`, `aieos_migrator`, `aieos_runtime`, owners, dispatchers).
4. Runs `alembic upgrade head` through `aieos_migrator`.
5. Verifies `alembic_version = tosd090001`.
6. Applies runtime grants required by current schemas.
7. Starts the FastAPI API under the Python debugger at http://127.0.0.1:8080.

The launcher is `tools/dev/run_local_api.py`. It is separate from the production entrypoint `src/aieos/platform/runtime/entrypoints/api_main.py`.

---

## API Endpoints

| URL | Purpose |
|-----|---------|
| http://127.0.0.1:8080/docs | Swagger UI (title: **AIEOS HTTP API**) |
| http://127.0.0.1:8080/livez | Process liveness |
| http://127.0.0.1:8080/readyz | PostgreSQL readiness |

---

## Local Authentication (Swagger)

LOCAL DEVELOPMENT ONLY — NEVER PRODUCTION.

For authenticated routes in Swagger:

1. Click **Authorize**.
2. Enter bearer token: `aieos-local-dev`
3. Send the required tenant header (`X-AIEOS-Tenant-Id`) with the fixed local tenant UUID from `tools/dev/local_config.py`.

Fixed local identity (deterministic):

- Tenant and principal UUIDs are defined in `tools/dev/local_config.py`.
- Production JWT/JWKS is not used locally; adapters exist only in the local launcher.
- `/livez` reports `release.git_sha` from the **current Git HEAD** (`git rev-parse HEAD`) at F5 startup.

---

## CURRENT LOCAL CAPABILITIES

### A. Usable now (local PostgreSQL + local adapters)

- Operational health: `/livez`, `/readyz`
- Teaching Work reads and mutations backed by local PostgreSQL, including `teaching_work_create`
- Teaching Assignment reads and mutations (where no external dependency is required)
- Content reads and mutations that do not require real AIStor blob storage or production authorization kernel seed data
- Swagger exploration of registered routes with local bearer token `aieos-local-dev`
- Mutations are enabled locally through the governed mutation interlock (`AIEOS_API_MUTATION_ACTIVATION=ENABLED` with matching local Git SHA and artifact digest)

### B. Limited or unavailable without external integrations

- **Real AI generation** (`teaching_work_generate`, AI worksheet flows): requires OpenAI or configured model gateway credentials not included in this checkpoint
- **School Context class lists**: uses development synthetic data only; no live school information system
- **Production JWT/JWKS authentication**: not used locally; local bearer token only
- **AIStor blob ingest/asset flows**: local development uses permissive asset adapters; no real object storage backend
- **Production authorization kernel grants**: local development uses permissive development authorization adapters; security tables are not pre-seeded with tenant membership data

If a Swagger operation fails with an external dependency error, check this section before assuming a product defect.

---

## Database Connection Details

LOCAL DEVELOPMENT ONLY.

| Setting | Value |
|---------|-------|
| Host | `127.0.0.1` |
| Port | `55432` |
| Database | `aieos` |
| Inspection / admin user | `aieos_bootstrap` |
| Inspection password | `aieos_test` |
| Application runtime user | `aieos_runtime` |
| Runtime password | `aieos_test` |
| Migration user | `aieos_migrator` |
| Migrator password | `aieos_test` |

Use **`aieos_bootstrap`** when you want to see all local schemas and tables. Row-level security limits what `aieos_runtime` can see.

### Docker

| Setting | Value |
|---------|-------|
| Container | `aieos-local-postgres` |
| Image | `postgres:18` |
| Volume | `aieos-local-postgres-data` (mounted at `/var/lib/postgresql` per PostgreSQL 18 image requirements) |

---

## Client Setup

### DBeaver

1. New connection → PostgreSQL.
2. Host `127.0.0.1`, Port `55432`, Database `aieos`.
3. User `aieos_bootstrap`, Password `aieos_test`.
4. Test connection → Finish.

### pgAdmin

1. Register → Server.
2. Connection tab: Host `127.0.0.1`, Port `55432`, Maintenance database `aieos`.
3. Username `aieos_bootstrap`, Password `aieos_test`.

### Cursor / VS Code PostgreSQL Extension

Use the same host, port, database, user, and password as above.

---

## Useful SQL

List non-system tables:

```sql
SELECT
    table_schema,
    table_name
FROM information_schema.tables
WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
ORDER BY table_schema, table_name;
```

Current migration head:

```sql
SELECT version_num
FROM alembic_version;
```

Expected: `tosd090001`

### DEV08-I01 tables (may be empty)

```sql
SELECT *
FROM assessment.classroom_assessments
ORDER BY created_at DESC;
```

### DEV07-I01 tables (may be empty)

```sql
SELECT *
FROM teaching.executions
ORDER BY created_at DESC;

SELECT *
FROM teaching.execution_content_bindings;

SELECT *
FROM teaching.execution_observations
ORDER BY recorded_at DESC;
```

### Other useful tables

```sql
SELECT * FROM teaching.works;
SELECT * FROM teaching.assignments;
SELECT * FROM content.contents;
SELECT * FROM content.content_versions;
SELECT * FROM content.review_decisions;
SELECT * FROM content.publications;
```

---

## Manual Tasks

| Task | Command |
|------|---------|
| DB up + migrate | `uv run python tools/dev/local_db.py up` |
| DB status | `uv run python tools/dev/local_db.py status` |
| DB stop | `uv run python tools/dev/local_db.py stop` |
| DB reset | `uv run python tools/dev/local_db.py reset --confirm` |
| Open docs | Task **AIEOS: Open API Docs** |

---

## Security Posture

- Production entrypoint and `src/aieos/platform/runtime/config.py` are unchanged.
- No production JWT secrets, AI keys, AIStor credentials, or production database hosts are required.
- Local adapters are composed only by `tools/dev/run_local_api.py`.
- The API binds to loopback (`127.0.0.1:8080`) by default.

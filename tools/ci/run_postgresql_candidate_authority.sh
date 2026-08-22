#!/usr/bin/env bash
# Disposable PostgreSQL 18 ADR-AIEOS-045 candidate-authority CI handshake.
# Pins Infrastructure scripts; does not authorize production migration or daemons.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
INFRA_ROOT="${AIEOS_INFRA_ROOT:-${ROOT}/_infra}"

fail() { echo "CANDIDATE_AUTHORITY_FAIL: $*" >&2; exit 1; }
info() { echo "CANDIDATE_AUTHORITY_INFO: $*"; }

require_cmd() { command -v "$1" >/dev/null 2>&1 || fail "required command not found: $1"; }

require_cmd psql
require_cmd uv

PGHOST="${PGHOST:-localhost}"
PGPORT="${PGPORT:-5432}"
PGDATABASE="${PGDATABASE:-aieos_ci}"
PGUSER="${PGUSER:-postgres}"
export PGHOST PGPORT PGDATABASE PGUSER
export PGPASSWORD="${PGPASSWORD:?PGPASSWORD must be set}"

psql_exec() { psql -v ON_ERROR_STOP=1 -X -q "$@"; }
psql_query() { psql_exec -At "$@"; }

PGVERSION="$(psql_query -c "SELECT version()")"
case "$PGVERSION" in
  PostgreSQL\ 18.*) info "connected to ${PGVERSION}" ;;
  *) fail "expected PostgreSQL 18.x, got: ${PGVERSION}" ;;
esac

export AIEOS_DB_DEPLOYMENT_ADMIN_ROLE="${AIEOS_DB_DEPLOYMENT_ADMIN_ROLE:-aieos_db_deployment_admin}"
export AIEOS_SCHEMA_OWNER_ROLE="${AIEOS_SCHEMA_OWNER_ROLE:-aieos_content_owner}"
export AIEOS_SECURITY_SCHEMA_OWNER_ROLE="${AIEOS_SECURITY_SCHEMA_OWNER_ROLE:-aieos_security_owner}"
export AIEOS_ASSET_SCHEMA_OWNER_ROLE="${AIEOS_ASSET_SCHEMA_OWNER_ROLE:-aieos_asset_owner}"
export AIEOS_MIGRATOR_ROLE="${AIEOS_MIGRATOR_ROLE:-aieos_migrator}"
export AIEOS_RUNTIME_ROLE="${AIEOS_RUNTIME_ROLE:-aieos_runtime}"
export AIEOS_CONTENT_MIGRATION_RUNTIME_ROLE="${AIEOS_CONTENT_MIGRATION_RUNTIME_ROLE:-aieos_content_migration_runtime}"
export AIEOS_EVENT_DISPATCHER_ROLE="${AIEOS_EVENT_DISPATCHER_ROLE:-aieos_event_dispatcher}"
export AIEOS_WORKFLOW_DISPATCHER_ROLE="${AIEOS_WORKFLOW_DISPATCHER_ROLE:-aieos_workflow_dispatcher}"
export AIEOS_EVENT_CANDIDATE_READER_ROLE="${AIEOS_EVENT_CANDIDATE_READER_ROLE:-aieos_event_candidate_reader}"
export AIEOS_WORKFLOW_CANDIDATE_READER_ROLE="${AIEOS_WORKFLOW_CANDIDATE_READER_ROLE:-aieos_workflow_candidate_reader}"

CI_PASSWORD="ci_candidate_authority_only"

[[ -d "${INFRA_ROOT}/scripts/postgresql" ]] || fail "Infrastructure scripts missing under ${INFRA_ROOT}"

psql_exec <<SQL
DROP ROLE IF EXISTS ${AIEOS_EVENT_DISPATCHER_ROLE};
DROP ROLE IF EXISTS ${AIEOS_WORKFLOW_DISPATCHER_ROLE};
DROP ROLE IF EXISTS ${AIEOS_CONTENT_MIGRATION_RUNTIME_ROLE};
DROP ROLE IF EXISTS ${AIEOS_RUNTIME_ROLE};
DROP ROLE IF EXISTS ${AIEOS_MIGRATOR_ROLE};
DROP ROLE IF EXISTS ${AIEOS_EVENT_CANDIDATE_READER_ROLE};
DROP ROLE IF EXISTS ${AIEOS_WORKFLOW_CANDIDATE_READER_ROLE};
DROP ROLE IF EXISTS ${AIEOS_ASSET_SCHEMA_OWNER_ROLE};
DROP ROLE IF EXISTS ${AIEOS_SECURITY_SCHEMA_OWNER_ROLE};
DROP ROLE IF EXISTS ${AIEOS_SCHEMA_OWNER_ROLE};
DROP ROLE IF EXISTS ${AIEOS_DB_DEPLOYMENT_ADMIN_ROLE};
SQL

psql_exec <<SQL
CREATE ROLE ${AIEOS_DB_DEPLOYMENT_ADMIN_ROLE} LOGIN PASSWORD '${CI_PASSWORD}'
  CREATEROLE NOCREATEDB NOBYPASSRLS NOSUPERUSER NOREPLICATION;
CREATE ROLE ${AIEOS_SCHEMA_OWNER_ROLE}
  NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
CREATE ROLE ${AIEOS_SECURITY_SCHEMA_OWNER_ROLE}
  NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
CREATE ROLE ${AIEOS_ASSET_SCHEMA_OWNER_ROLE}
  NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
CREATE ROLE ${AIEOS_MIGRATOR_ROLE} LOGIN PASSWORD '${CI_PASSWORD}'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS NOINHERIT;
CREATE ROLE ${AIEOS_RUNTIME_ROLE} LOGIN PASSWORD '${CI_PASSWORD}'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
CREATE ROLE ${AIEOS_CONTENT_MIGRATION_RUNTIME_ROLE} LOGIN PASSWORD '${CI_PASSWORD}'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
CREATE ROLE ${AIEOS_EVENT_DISPATCHER_ROLE} LOGIN PASSWORD '${CI_PASSWORD}'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
CREATE ROLE ${AIEOS_WORKFLOW_DISPATCHER_ROLE} LOGIN PASSWORD '${CI_PASSWORD}'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
GRANT ${AIEOS_SCHEMA_OWNER_ROLE} TO ${AIEOS_MIGRATOR_ROLE};
GRANT ${AIEOS_SECURITY_SCHEMA_OWNER_ROLE} TO ${AIEOS_MIGRATOR_ROLE};
GRANT ${AIEOS_ASSET_SCHEMA_OWNER_ROLE} TO ${AIEOS_MIGRATOR_ROLE};
GRANT CONNECT, CREATE ON DATABASE ${PGDATABASE} TO ${AIEOS_SCHEMA_OWNER_ROLE};
GRANT CONNECT, CREATE ON DATABASE ${PGDATABASE} TO ${AIEOS_SECURITY_SCHEMA_OWNER_ROLE};
GRANT CONNECT, CREATE ON DATABASE ${PGDATABASE} TO ${AIEOS_ASSET_SCHEMA_OWNER_ROLE};
GRANT CONNECT ON DATABASE ${PGDATABASE} TO ${AIEOS_MIGRATOR_ROLE};
GRANT CONNECT ON DATABASE ${PGDATABASE} TO ${AIEOS_RUNTIME_ROLE};
GRANT CONNECT ON DATABASE ${PGDATABASE} TO ${AIEOS_CONTENT_MIGRATION_RUNTIME_ROLE};
GRANT CONNECT ON DATABASE ${PGDATABASE} TO ${AIEOS_EVENT_DISPATCHER_ROLE};
GRANT CONNECT ON DATABASE ${PGDATABASE} TO ${AIEOS_WORKFLOW_DISPATCHER_ROLE};
GRANT CONNECT ON DATABASE ${PGDATABASE} TO ${AIEOS_DB_DEPLOYMENT_ADMIN_ROLE};
GRANT USAGE, CREATE ON SCHEMA public TO ${AIEOS_SCHEMA_OWNER_ROLE};
GRANT USAGE ON SCHEMA public TO ${AIEOS_MIGRATOR_ROLE};
SQL

run_as_deployment_admin() {
  PGUSER="$AIEOS_DB_DEPLOYMENT_ADMIN_ROLE" PGPASSWORD="$CI_PASSWORD" "$@"
}

assert_no_migrator_jit() {
  local event_edge workflow_edge
  event_edge="$(psql_query -c "
    SELECT COUNT(*)
    FROM pg_auth_members am
    JOIN pg_roles granted ON granted.oid = am.roleid
    JOIN pg_roles member ON member.oid = am.member
    WHERE granted.rolname = '${AIEOS_EVENT_CANDIDATE_READER_ROLE}'
      AND member.rolname = '${AIEOS_MIGRATOR_ROLE}'
  ")"
  workflow_edge="$(psql_query -c "
    SELECT COUNT(*)
    FROM pg_auth_members am
    JOIN pg_roles granted ON granted.oid = am.roleid
    JOIN pg_roles member ON member.oid = am.member
    WHERE granted.rolname = '${AIEOS_WORKFLOW_CANDIDATE_READER_ROLE}'
      AND member.rolname = '${AIEOS_MIGRATOR_ROLE}'
  ")"
  [[ "$event_edge" == "0" ]] || fail "migrator still has event candidate-reader membership"
  [[ "$workflow_edge" == "0" ]] || fail "migrator still has workflow candidate-reader membership"
}

infra_revoke_and_baseline() {
  run_as_deployment_admin \
    "${INFRA_ROOT}/scripts/postgresql/revoke-candidate-migration-access.sh" \
    || fail "Infrastructure revoke-candidate-migration-access.sh failed"
  AIEOS_VERIFY_MODE=baseline run_as_deployment_admin \
    "${INFRA_ROOT}/scripts/postgresql/verify-candidate-readers.sh" \
    || fail "Infrastructure baseline verifier failed"
  assert_no_migrator_jit
}

assert_downgraded_state() {
  local count
  count="$(psql_query -c "
    SELECT COUNT(*) FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE (n.nspname, p.proname) IN (
      ('integration', 'list_outbox_dispatch_candidates'),
      ('workflow', 'list_start_intent_candidates'),
      ('workflow', 'list_command_intent_candidates')
    )
  ")"
  [[ "$count" == "0" ]] || fail "candidate functions still present after downgrade"

  count="$(psql_query -c "
    SELECT COUNT(*) FROM pg_policies
    WHERE (schemaname, tablename, policyname) IN (
      ('integration', 'outbox_messages', 'outbox_messages_event_candidate_reader_select'),
      ('workflow', 'workflow_start_intents', 'workflow_start_intents_workflow_candidate_reader_select'),
      ('workflow', 'workflow_command_intents', 'workflow_command_intents_workflow_candidate_reader_select')
    )
  ")"
  [[ "$count" == "0" ]] || fail "candidate RLS policies still present after downgrade"

  count="$(psql_query -c "
    SELECT COUNT(*) FROM pg_policies
    WHERE (schemaname, tablename, policyname) IN (
      ('integration', 'outbox_messages', 'outbox_messages_tenant_isolation'),
      ('workflow', 'workflow_start_intents', 'workflow_start_intents_tenant_isolation'),
      ('workflow', 'workflow_command_intents', 'workflow_command_intents_tenant_isolation')
    )
  ")"
  [[ "$count" == "3" ]] || fail "original universal tenant policies not restored after downgrade"

  count="$(psql_query -c "
    SELECT COUNT(*) FROM information_schema.column_privileges
    WHERE grantee IN (
      '${AIEOS_EVENT_CANDIDATE_READER_ROLE}',
      '${AIEOS_WORKFLOW_CANDIDATE_READER_ROLE}'
    )
      AND privilege_type = 'SELECT'
      AND (
        (table_schema = 'integration' AND table_name = 'outbox_messages'
          AND column_name IN ('tenant_id', 'status', 'available_at', 'claimed_until'))
        OR (table_schema = 'workflow'
          AND table_name IN ('workflow_start_intents', 'workflow_command_intents')
          AND column_name IN ('tenant_id', 'status', 'available_at', 'claimed_until'))
      )
  ")"
  [[ "$count" == "0" ]] || fail "candidate scheduling-column grants still present after downgrade"

  count="$(psql_query -c "
    SELECT COUNT(*)
    FROM (
      SELECT 1
      WHERE has_schema_privilege(
        '${AIEOS_EVENT_CANDIDATE_READER_ROLE}', 'integration', 'CREATE'
      )
      UNION ALL
      SELECT 1
      WHERE has_schema_privilege(
        '${AIEOS_WORKFLOW_CANDIDATE_READER_ROLE}', 'workflow', 'CREATE'
      )
    ) x
  ")"
  [[ "$count" == "0" ]] || fail "candidate schema CREATE from adra045001 still present after downgrade"

  count="$(psql_query -c "
    SELECT COUNT(*) FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'integration'
      AND c.relkind = 'i'
      AND c.relname IN (
        'ix_outbox_messages_candidate_pending',
        'ix_outbox_messages_candidate_claimed'
      )
  ")"
  [[ "$count" == "0" ]] || fail "EVENT candidate indexes still present after downgrade"

  count="$(psql_query -c "
    SELECT COUNT(*) FROM pg_roles
    WHERE rolname IN (
      '${AIEOS_EVENT_CANDIDATE_READER_ROLE}',
      '${AIEOS_WORKFLOW_CANDIDATE_READER_ROLE}'
    )
  ")"
  [[ "$count" == "2" ]] || fail "candidate roles must survive downgrade"

  count="$(psql_query -c "
    SELECT COUNT(*) FROM pg_roles
    WHERE rolname IN (
      '${AIEOS_EVENT_CANDIDATE_READER_ROLE}',
      '${AIEOS_WORKFLOW_CANDIDATE_READER_ROLE}'
    )
      AND rolcanlogin = false
      AND rolbypassrls = false
  ")"
  [[ "$count" == "2" ]] || fail "candidate roles must remain NOLOGIN / NOBYPASSRLS"

  count="$(psql_query -c "
    SELECT COUNT(*) FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE (
      (n.nspname = 'integration' AND c.relname = 'outbox_messages')
      OR (n.nspname = 'workflow' AND c.relname IN (
        'workflow_start_intents', 'workflow_command_intents'
      ))
    )
      AND c.relrowsecurity = true
      AND c.relforcerowsecurity = true
  ")"
  [[ "$count" == "3" ]] || fail "FORCE RLS must remain enabled on all three queues"

  assert_no_migrator_jit
}

chmod +x \
  "${INFRA_ROOT}/scripts/postgresql/bootstrap-candidate-readers.sh" \
  "${INFRA_ROOT}/scripts/postgresql/grant-candidate-migration-access.sh" \
  "${INFRA_ROOT}/scripts/postgresql/revoke-candidate-migration-access.sh" \
  "${INFRA_ROOT}/scripts/postgresql/verify-candidate-readers.sh"

run_as_deployment_admin "${INFRA_ROOT}/scripts/postgresql/bootstrap-candidate-readers.sh"
AIEOS_VERIFY_MODE=baseline run_as_deployment_admin \
  "${INFRA_ROOT}/scripts/postgresql/verify-candidate-readers.sh"

export AIEOS_DATABASE_URL="postgresql+psycopg://${AIEOS_MIGRATOR_ROLE}:${CI_PASSWORD}@${PGHOST}:${PGPORT}/${PGDATABASE}"

run_alembic_as_migrator() {
  (
    export PGUSER="$AIEOS_MIGRATOR_ROLE"
    export PGPASSWORD="$CI_PASSWORD"
    cd "$ROOT"
    uv run alembic "$@"
  )
}

run_as_deployment_admin "${INFRA_ROOT}/scripts/postgresql/grant-candidate-migration-access.sh"
AIEOS_VERIFY_MODE=jit run_as_deployment_admin \
  "${INFRA_ROOT}/scripts/postgresql/verify-candidate-readers.sh"
run_alembic_as_migrator upgrade head
head_rev="$(psql_query -c "SELECT version_num FROM alembic_version")"
[[ "$head_rev" == "adra045001" ]] || fail "expected head adra045001 after upgrade, got ${head_rev}"
infra_revoke_and_baseline

# Isolated downgrade under a fresh JIT window (not shared with re-upgrade).
run_as_deployment_admin "${INFRA_ROOT}/scripts/postgresql/grant-candidate-migration-access.sh"
AIEOS_VERIFY_MODE=jit run_as_deployment_admin \
  "${INFRA_ROOT}/scripts/postgresql/verify-candidate-readers.sh"
run_alembic_as_migrator downgrade pedi10b6001
down_rev="$(psql_query -c "SELECT version_num FROM alembic_version")"
[[ "$down_rev" == "pedi10b6001" ]] || fail "expected pedi10b6001 after downgrade, got ${down_rev}"
infra_revoke_and_baseline
assert_downgraded_state

# Fresh JIT re-upgrade
run_as_deployment_admin "${INFRA_ROOT}/scripts/postgresql/grant-candidate-migration-access.sh"
AIEOS_VERIFY_MODE=jit run_as_deployment_admin \
  "${INFRA_ROOT}/scripts/postgresql/verify-candidate-readers.sh"
run_alembic_as_migrator upgrade head
head_rev="$(psql_query -c "SELECT version_num FROM alembic_version")"
[[ "$head_rev" == "adra045001" ]] || fail "expected adra045001 after re-upgrade, got ${head_rev}"
infra_revoke_and_baseline

info "offline alembic SQL expanded acceptance"
offline_sql="$(
  cd "$ROOT"
  AIEOS_DATABASE_URL="postgresql+psycopg://offline-check/unused" \
    uv run alembic upgrade head --sql
)"
offline_assert() {
  local needle="$1"
  local message="$2"
  # Prefer here-string over echo|grep to avoid pipefail/SIGPIPE false failures.
  grep -Fq -- "$needle" <<<"$offline_sql" || fail "$message"
}
offline_assert 'list_outbox_dispatch_candidates' \
  "offline SQL missing event candidate function"
offline_assert 'list_start_intent_candidates' \
  "offline SQL missing workflow start candidate function"
offline_assert 'list_command_intent_candidates' \
  "offline SQL missing workflow command candidate function"
grep -Fiq -- 'SECURITY DEFINER' <<<"$offline_sql" \
  || fail "offline SQL missing SECURITY DEFINER"
offline_assert 'SET search_path TO pg_catalog, pg_temp' \
  "offline SQL missing approved search_path"
offline_assert 'SET LOCAL ROLE' \
  "offline SQL missing SET LOCAL ROLE transitions"
grep -Fiq -- 'REVOKE ALL ON FUNCTION' <<<"$offline_sql" \
  || fail "offline SQL missing PUBLIC EXECUTE revoke"
grep -Fiq -- 'GRANT CREATE ON SCHEMA' <<<"$offline_sql" \
  || fail "offline SQL missing candidate CREATE grant choreography"
grep -Fiq -- 'REVOKE CREATE ON SCHEMA' <<<"$offline_sql" \
  || fail "offline SQL missing candidate CREATE revoke choreography"
if grep -Eiq -- 'CREATE[[:space:]]+ROLE' <<<"$offline_sql"; then
  fail "offline SQL contains CREATE ROLE"
fi
if grep -Eiq -- 'ALTER[[:space:]]+ROLE' <<<"$offline_sql"; then
  fail "offline SQL contains ALTER ROLE"
fi
if grep -Eiq -- 'GRANT[[:space:]]+aieos_(event|workflow)_candidate_reader[[:space:]]+TO' <<<"$offline_sql"; then
  fail "offline SQL grants candidate-reader membership to migrator"
fi
if grep -Eiq -- 'password|digitalocean|doctl|api\.digitalocean' <<<"$offline_sql"; then
  fail "offline SQL contains forbidden credential/token material"
fi
if grep -Eiq -- 'postgresql(\+[a-z]+)?://[^[:space:]]+:[^[:space:]]+@' <<<"$offline_sql"; then
  fail "offline SQL contains production-like connection URI"
fi

# Fresh JIT for dedicated pytest — Backend fixture must not add a second grant edge
run_as_deployment_admin "${INFRA_ROOT}/scripts/postgresql/grant-candidate-migration-access.sh"
AIEOS_VERIFY_MODE=jit run_as_deployment_admin \
  "${INFRA_ROOT}/scripts/postgresql/verify-candidate-readers.sh"

export AIEOS_TEST_CANDIDATE_JIT_EXTERNAL=1
export AIEOS_TEST_BOOTSTRAP_DATABASE_URL="postgresql+psycopg://${PGUSER}:${PGPASSWORD}@${PGHOST}:${PGPORT}/${PGDATABASE}"
export AIEOS_TEST_DATABASE_URL="postgresql+psycopg://${AIEOS_MIGRATOR_ROLE}:${CI_PASSWORD}@${PGHOST}:${PGPORT}/${PGDATABASE}"
export AIEOS_TEST_RUNTIME_DATABASE_URL="postgresql+psycopg://${AIEOS_RUNTIME_ROLE}:${CI_PASSWORD}@${PGHOST}:${PGPORT}/${PGDATABASE}"
export AIEOS_TEST_MIGRATION_RUNTIME_DATABASE_URL="postgresql+psycopg://${AIEOS_CONTENT_MIGRATION_RUNTIME_ROLE}:${CI_PASSWORD}@${PGHOST}:${PGPORT}/${PGDATABASE}"
export AIEOS_TEST_WORKFLOW_DISPATCHER_DATABASE_URL="postgresql+psycopg://${AIEOS_WORKFLOW_DISPATCHER_ROLE}:${CI_PASSWORD}@${PGHOST}:${PGPORT}/${PGDATABASE}"
export AIEOS_TEST_EVENT_DISPATCHER_DATABASE_URL="postgresql+psycopg://${AIEOS_EVENT_DISPATCHER_ROLE}:${CI_PASSWORD}@${PGHOST}:${PGPORT}/${PGDATABASE}"

cd "$ROOT"
# -s keeps EXPLAIN (ANALYZE, BUFFERS) plan lines in CI logs (section 15).
uv run pytest -v -s -m postgres_candidate_authority

# Hard-gated final cleanup — Infrastructure revoke + baseline must pass.
# Superuser helper is diagnostic only and must not convert failure into green CI.
if ! run_as_deployment_admin \
  "${INFRA_ROOT}/scripts/postgresql/revoke-candidate-migration-access.sh"; then
  info "diagnostic: attempting disposable superuser REVOKE after Infrastructure revoke failure"
  env PGUSER=postgres PGPASSWORD="${PGPASSWORD}" \
    psql -v ON_ERROR_STOP=0 -X -q \
    -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename = '${AIEOS_MIGRATOR_ROLE}' AND pid <> pg_backend_pid()" \
    -c "REVOKE ${AIEOS_EVENT_CANDIDATE_READER_ROLE} FROM ${AIEOS_MIGRATOR_ROLE}" \
    -c "REVOKE ${AIEOS_WORKFLOW_CANDIDATE_READER_ROLE} FROM ${AIEOS_MIGRATOR_ROLE}" \
    || true
  fail "Infrastructure revoke-candidate-migration-access.sh failed after pytest"
fi

AIEOS_VERIFY_MODE=baseline run_as_deployment_admin \
  "${INFRA_ROOT}/scripts/postgresql/verify-candidate-readers.sh" \
  || fail "final Infrastructure baseline verifier failed after pytest"
assert_no_migrator_jit

info "postgresql candidate-authority CI proof complete"

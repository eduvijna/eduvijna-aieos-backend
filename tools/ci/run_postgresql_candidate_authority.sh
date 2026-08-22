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

revoke_jit_as_superuser() {
  # Disposable CI cleanup: Infrastructure revoke is authoritative in production
  # administration, but the GitHub postgres service superuser is used here to
  # guarantee catalogue cleanup after Alembic exits.
  psql_exec -c "REVOKE ${AIEOS_EVENT_CANDIDATE_READER_ROLE} FROM ${AIEOS_MIGRATOR_ROLE}"
  psql_exec -c "REVOKE ${AIEOS_WORKFLOW_CANDIDATE_READER_ROLE} FROM ${AIEOS_MIGRATOR_ROLE}"
  assert_no_migrator_jit
}

run_as_deployment_admin "${INFRA_ROOT}/scripts/postgresql/grant-candidate-migration-access.sh"
AIEOS_VERIFY_MODE=jit run_as_deployment_admin \
  "${INFRA_ROOT}/scripts/postgresql/verify-candidate-readers.sh"
run_alembic_as_migrator upgrade head
head_rev="$(psql_query -c "SELECT version_num FROM alembic_version")"
[[ "$head_rev" == "adra045001" ]] || fail "expected head adra045001 after upgrade, got ${head_rev}"
run_as_deployment_admin "${INFRA_ROOT}/scripts/postgresql/revoke-candidate-migration-access.sh" \
  || info "infrastructure revoke returned non-zero; applying superuser cleanup"
revoke_jit_as_superuser

run_as_deployment_admin "${INFRA_ROOT}/scripts/postgresql/grant-candidate-migration-access.sh"
run_alembic_as_migrator downgrade pedi10b6001
down_rev="$(psql_query -c "SELECT version_num FROM alembic_version")"
[[ "$down_rev" == "pedi10b6001" ]] || fail "expected pedi10b6001 after downgrade, got ${down_rev}"
run_alembic_as_migrator upgrade head
head_rev="$(psql_query -c "SELECT version_num FROM alembic_version")"
[[ "$head_rev" == "adra045001" ]] || fail "expected adra045001 after re-upgrade, got ${head_rev}"
run_as_deployment_admin "${INFRA_ROOT}/scripts/postgresql/revoke-candidate-migration-access.sh" \
  || info "infrastructure revoke returned non-zero; applying superuser cleanup"
revoke_jit_as_superuser
AIEOS_VERIFY_MODE=baseline run_as_deployment_admin \
  "${INFRA_ROOT}/scripts/postgresql/verify-candidate-readers.sh"

info "offline alembic SQL must not CREATE ROLE"
offline_sql="$(
  cd "$ROOT"
  AIEOS_DATABASE_URL="postgresql+psycopg://offline-check/unused" \
    uv run alembic upgrade head --sql
)"
if echo "$offline_sql" | grep -qiE 'CREATE[[:space:]]+ROLE'; then
  fail "offline SQL contains CREATE ROLE"
fi
if echo "$offline_sql" | grep -qiE 'ALTER[[:space:]]+ROLE'; then
  fail "offline SQL contains ALTER ROLE"
fi

# Re-grant JIT so session-scoped pytest postgres18 fixture can cycle migrations.
run_as_deployment_admin "${INFRA_ROOT}/scripts/postgresql/grant-candidate-migration-access.sh"

export AIEOS_TEST_BOOTSTRAP_DATABASE_URL="postgresql+psycopg://${PGUSER}:${PGPASSWORD}@${PGHOST}:${PGPORT}/${PGDATABASE}"
export AIEOS_TEST_DATABASE_URL="postgresql+psycopg://${AIEOS_MIGRATOR_ROLE}:${CI_PASSWORD}@${PGHOST}:${PGPORT}/${PGDATABASE}"
export AIEOS_TEST_RUNTIME_DATABASE_URL="postgresql+psycopg://${AIEOS_RUNTIME_ROLE}:${CI_PASSWORD}@${PGHOST}:${PGPORT}/${PGDATABASE}"
export AIEOS_TEST_MIGRATION_RUNTIME_DATABASE_URL="postgresql+psycopg://${AIEOS_CONTENT_MIGRATION_RUNTIME_ROLE}:${CI_PASSWORD}@${PGHOST}:${PGPORT}/${PGDATABASE}"
export AIEOS_TEST_WORKFLOW_DISPATCHER_DATABASE_URL="postgresql+psycopg://${AIEOS_WORKFLOW_DISPATCHER_ROLE}:${CI_PASSWORD}@${PGHOST}:${PGPORT}/${PGDATABASE}"
export AIEOS_TEST_EVENT_DISPATCHER_DATABASE_URL="postgresql+psycopg://${AIEOS_EVENT_DISPATCHER_ROLE}:${CI_PASSWORD}@${PGHOST}:${PGPORT}/${PGDATABASE}"

cd "$ROOT"
uv run pytest -v -m postgres_candidate_authority

run_as_deployment_admin "${INFRA_ROOT}/scripts/postgresql/revoke-candidate-migration-access.sh" || true
revoke_jit_as_superuser

info "postgresql candidate-authority CI proof complete"

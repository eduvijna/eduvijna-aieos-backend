"""PostgreSQL 18 fixtures for GCI-I02. Never connect to production."""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from tests.dbutil import (
    REPO_ROOT,
    clear_asset_audit_rows_for_schema_downgrade,
    set_tenant,
)

__all__ = ["REPO_ROOT", "set_tenant"]

CONTAINER_NAME = "aieos-gci-i02-pg"
POSTGRES_IMAGE = "postgres:18"
BOOTSTRAP_USER = "aieos_bootstrap"
SCHEMA_OWNER_ROLE = "aieos_content_owner"
SECURITY_SCHEMA_OWNER_ROLE = "aieos_security_owner"
ASSET_SCHEMA_OWNER_ROLE = "aieos_asset_owner"
MIGRATOR_USER = "aieos_migrator"
RUNTIME_USER = "aieos_runtime"
MIGRATION_RUNTIME_USER = "aieos_content_migration_runtime"
WORKFLOW_DISPATCHER_USER = "aieos_workflow_dispatcher"
EVENT_DISPATCHER_USER = "aieos_event_dispatcher"
EVENT_CANDIDATE_READER_ROLE = "aieos_event_candidate_reader"
WORKFLOW_CANDIDATE_READER_ROLE = "aieos_workflow_candidate_reader"
DB_NAME = "aieos"
DB_PASSWORD = "aieos_test"
HOST_PORT = os.environ.get("AIEOS_TEST_PG_PORT", "55432")


def bootstrap_url(port: str) -> str:
    return (
        f"postgresql+psycopg://{BOOTSTRAP_USER}:{DB_PASSWORD}"
        f"@127.0.0.1:{port}/{DB_NAME}"
    )


def migrator_url(port: str) -> str:
    return (
        f"postgresql+psycopg://{MIGRATOR_USER}:{DB_PASSWORD}"
        f"@127.0.0.1:{port}/{DB_NAME}"
    )


def runtime_url(port: str) -> str:
    return (
        f"postgresql+psycopg://{RUNTIME_USER}:{DB_PASSWORD}"
        f"@127.0.0.1:{port}/{DB_NAME}"
    )


def migration_runtime_url(port: str) -> str:
    return (
        f"postgresql+psycopg://{MIGRATION_RUNTIME_USER}:{DB_PASSWORD}"
        f"@127.0.0.1:{port}/{DB_NAME}"
    )


def workflow_dispatcher_url(port: str) -> str:
    return (
        f"postgresql+psycopg://{WORKFLOW_DISPATCHER_USER}:{DB_PASSWORD}"
        f"@127.0.0.1:{port}/{DB_NAME}"
    )


def event_dispatcher_url(port: str) -> str:
    return (
        f"postgresql+psycopg://{EVENT_DISPATCHER_USER}:{DB_PASSWORD}"
        f"@127.0.0.1:{port}/{DB_NAME}"
    )


def alembic_config(url: str) -> Config:
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    os.environ["AIEOS_DATABASE_URL"] = url
    os.environ["AIEOS_SCHEMA_OWNER_ROLE"] = SCHEMA_OWNER_ROLE
    os.environ["AIEOS_SECURITY_SCHEMA_OWNER_ROLE"] = SECURITY_SCHEMA_OWNER_ROLE
    os.environ["AIEOS_ASSET_SCHEMA_OWNER_ROLE"] = ASSET_SCHEMA_OWNER_ROLE
    os.environ["AIEOS_RUNTIME_ROLE"] = RUNTIME_USER
    os.environ["AIEOS_CONTENT_MIGRATION_RUNTIME_ROLE"] = MIGRATION_RUNTIME_USER
    os.environ["AIEOS_EVENT_DISPATCHER_ROLE"] = EVENT_DISPATCHER_USER
    os.environ["AIEOS_WORKFLOW_DISPATCHER_ROLE"] = WORKFLOW_DISPATCHER_USER
    os.environ["AIEOS_EVENT_CANDIDATE_READER_ROLE"] = EVENT_CANDIDATE_READER_ROLE
    os.environ["AIEOS_WORKFLOW_CANDIDATE_READER_ROLE"] = WORKFLOW_CANDIDATE_READER_ROLE
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def wait_for_engine(url: str, attempts: int = 40) -> Engine:
    last_error: Exception | None = None
    for _ in range(attempts):
        engine = create_engine(url, isolation_level="AUTOCOMMIT")
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return engine
        except Exception as exc:  # noqa: BLE001 — wait loop
            last_error = exc
            engine.dispose()
            time.sleep(1)
    raise RuntimeError(f"PostgreSQL 18 did not become ready: {last_error}")


def start_postgres() -> str:
    subprocess.run(["docker", "rm", "-f", CONTAINER_NAME], check=False, capture_output=True)
    result = subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            CONTAINER_NAME,
            "-e",
            f"POSTGRES_USER={BOOTSTRAP_USER}",
            "-e",
            f"POSTGRES_PASSWORD={DB_PASSWORD}",
            "-e",
            f"POSTGRES_DB={DB_NAME}",
            "-p",
            f"{HOST_PORT}:5432",
            POSTGRES_IMAGE,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    if not result.stdout.strip():
        raise RuntimeError(f"docker run failed: {result.stderr}")
    return HOST_PORT


def provision_identities(bootstrap: Engine) -> None:
    """Create ephemeral owner/migrator/runtime roles. Not production provisioning."""
    with bootstrap.connect() as conn:
        conn.execute(
            text(
                f"""
                DO $$
                BEGIN
                    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{SCHEMA_OWNER_ROLE}') THEN
                        CREATE ROLE {SCHEMA_OWNER_ROLE}
                            NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
                    END IF;
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_roles WHERE rolname = '{SECURITY_SCHEMA_OWNER_ROLE}'
                    ) THEN
                        CREATE ROLE {SECURITY_SCHEMA_OWNER_ROLE}
                            NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
                    END IF;
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_roles WHERE rolname = '{ASSET_SCHEMA_OWNER_ROLE}'
                    ) THEN
                        CREATE ROLE {ASSET_SCHEMA_OWNER_ROLE}
                            NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
                    END IF;
                    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{MIGRATOR_USER}') THEN
                        CREATE ROLE {MIGRATOR_USER} LOGIN PASSWORD '{DB_PASSWORD}'
                            NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS NOINHERIT;
                    END IF;
                    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{RUNTIME_USER}') THEN
                        CREATE ROLE {RUNTIME_USER} LOGIN PASSWORD '{DB_PASSWORD}'
                            NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
                    END IF;
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_roles WHERE rolname = '{MIGRATION_RUNTIME_USER}'
                    ) THEN
                        CREATE ROLE {MIGRATION_RUNTIME_USER} LOGIN PASSWORD '{DB_PASSWORD}'
                            NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
                    END IF;
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_roles WHERE rolname = '{WORKFLOW_DISPATCHER_USER}'
                    ) THEN
                        CREATE ROLE {WORKFLOW_DISPATCHER_USER} LOGIN PASSWORD '{DB_PASSWORD}'
                            NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
                    END IF;
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_roles WHERE rolname = '{EVENT_DISPATCHER_USER}'
                    ) THEN
                        CREATE ROLE {EVENT_DISPATCHER_USER} LOGIN PASSWORD '{DB_PASSWORD}'
                            NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
                    END IF;
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_roles
                        WHERE rolname = '{EVENT_CANDIDATE_READER_ROLE}'
                    ) THEN
                        CREATE ROLE {EVENT_CANDIDATE_READER_ROLE}
                            NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
                            NOREPLICATION NOBYPASSRLS;
                    END IF;
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_roles
                        WHERE rolname = '{WORKFLOW_CANDIDATE_READER_ROLE}'
                    ) THEN
                        CREATE ROLE {WORKFLOW_CANDIDATE_READER_ROLE}
                            NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
                            NOREPLICATION NOBYPASSRLS;
                    END IF;
                END
                $$
                """
            )
        )
        conn.execute(text(f"GRANT {SCHEMA_OWNER_ROLE} TO {MIGRATOR_USER}"))
        conn.execute(text(f"GRANT {SECURITY_SCHEMA_OWNER_ROLE} TO {MIGRATOR_USER}"))
        conn.execute(text(f"GRANT {ASSET_SCHEMA_OWNER_ROLE} TO {MIGRATOR_USER}"))
        # Ephemeral JIT SET membership for ADR-AIEOS-045 function ownership choreography.
        # Production JIT grant/revoke remains Infrastructure-owned; Alembic never GRANTs this.
        # Dedicated postgresql-candidate-authority CI sets AIEOS_TEST_CANDIDATE_JIT_EXTERNAL=1
        # so Infrastructure scripts alone own the migrator->candidate JIT edge.
        if os.environ.get("AIEOS_TEST_CANDIDATE_JIT_EXTERNAL", "").strip() != "1":
            conn.execute(
                text(
                    f"GRANT {EVENT_CANDIDATE_READER_ROLE} TO {MIGRATOR_USER} "
                    f"WITH ADMIN FALSE, INHERIT FALSE, SET TRUE"
                )
            )
            conn.execute(
                text(
                    f"GRANT {WORKFLOW_CANDIDATE_READER_ROLE} TO {MIGRATOR_USER} "
                    f"WITH ADMIN FALSE, INHERIT FALSE, SET TRUE"
                )
            )
        db_name = conn.execute(text("SELECT current_database()")).scalar_one()
        conn.execute(
            text(f"GRANT CONNECT, CREATE ON DATABASE {db_name} TO {SCHEMA_OWNER_ROLE}")
        )
        conn.execute(
            text(
                f"GRANT CONNECT, CREATE ON DATABASE {db_name} "
                f"TO {SECURITY_SCHEMA_OWNER_ROLE}"
            )
        )
        conn.execute(
            text(
                f"GRANT CONNECT, CREATE ON DATABASE {db_name} "
                f"TO {ASSET_SCHEMA_OWNER_ROLE}"
            )
        )
        conn.execute(text(f"GRANT CONNECT ON DATABASE {db_name} TO {MIGRATOR_USER}"))
        conn.execute(text(f"GRANT CONNECT ON DATABASE {db_name} TO {RUNTIME_USER}"))
        conn.execute(
            text(f"GRANT CONNECT ON DATABASE {db_name} TO {MIGRATION_RUNTIME_USER}")
        )
        conn.execute(
            text(f"GRANT CONNECT ON DATABASE {db_name} TO {WORKFLOW_DISPATCHER_USER}")
        )
        conn.execute(
            text(f"GRANT CONNECT ON DATABASE {db_name} TO {EVENT_DISPATCHER_USER}")
        )
        conn.execute(text(f"GRANT USAGE, CREATE ON SCHEMA public TO {SCHEMA_OWNER_ROLE}"))
        conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {MIGRATOR_USER}"))


def provision_runtime_grants(bootstrap: Engine) -> None:
    with bootstrap.connect() as conn:
        with conn.begin():
            conn.execute(text(f"GRANT USAGE ON SCHEMA content TO {RUNTIME_USER}"))
            conn.execute(
                text(f"GRANT SELECT, INSERT, UPDATE ON content.contents TO {RUNTIME_USER}")
            )
            conn.execute(
                text(f"GRANT SELECT, INSERT ON content.content_versions TO {RUNTIME_USER}")
            )
            conn.execute(text(f"REVOKE DELETE ON content.contents FROM {RUNTIME_USER}"))
            conn.execute(
                text(f"REVOKE UPDATE, DELETE ON content.content_versions FROM {RUNTIME_USER}")
            )
            conn.execute(
                text(
                    f"GRANT EXECUTE ON FUNCTION content.current_tenant_id() TO {RUNTIME_USER}"
                )
            )
            conn.execute(
                text(
                    f"GRANT SELECT, INSERT ON content.review_decisions TO {RUNTIME_USER}"
                )
            )
            conn.execute(
                text(
                    f"REVOKE UPDATE, DELETE ON content.review_decisions FROM {RUNTIME_USER}"
                )
            )
            conn.execute(
                text(
                    f"GRANT SELECT, INSERT ON content.publications TO {RUNTIME_USER}"
                )
            )
            conn.execute(
                text(
                    f"REVOKE UPDATE, DELETE ON content.publications FROM {RUNTIME_USER}"
                )
            )
            conn.execute(
                text(
                    f"GRANT SELECT, INSERT ON content.version_asset_refs TO {RUNTIME_USER}"
                )
            )
            conn.execute(
                text(
                    f"REVOKE UPDATE, DELETE ON content.version_asset_refs FROM {RUNTIME_USER}"
                )
            )
            conn.execute(
                text(
                    f"REVOKE ALL ON content.migration_import_records FROM {RUNTIME_USER}"
                )
            )
            conn.execute(
                text(
                    f"GRANT SELECT ON content.migration_import_records TO {RUNTIME_USER}"
                )
            )
            conn.execute(
                text(
                    f"REVOKE INSERT, UPDATE, DELETE ON content.migration_import_records "
                    f"FROM {RUNTIME_USER}"
                )
            )
            conn.execute(text(f"GRANT USAGE ON SCHEMA content TO {MIGRATION_RUNTIME_USER}"))
            conn.execute(
                text(
                    f"GRANT SELECT, INSERT, UPDATE ON content.contents "
                    f"TO {MIGRATION_RUNTIME_USER}"
                )
            )
            conn.execute(
                text(
                    f"GRANT SELECT, INSERT ON content.content_versions "
                    f"TO {MIGRATION_RUNTIME_USER}"
                )
            )
            conn.execute(
                text(
                    f"REVOKE UPDATE, DELETE ON content.content_versions "
                    f"FROM {MIGRATION_RUNTIME_USER}"
                )
            )
            conn.execute(
                text(f"REVOKE DELETE ON content.contents FROM {MIGRATION_RUNTIME_USER}")
            )
            conn.execute(
                text(
                    f"GRANT SELECT, INSERT ON content.version_asset_refs "
                    f"TO {MIGRATION_RUNTIME_USER}"
                )
            )
            conn.execute(
                text(
                    f"REVOKE UPDATE, DELETE ON content.version_asset_refs "
                    f"FROM {MIGRATION_RUNTIME_USER}"
                )
            )
            conn.execute(
                text(
                    f"GRANT SELECT, INSERT, UPDATE ON content.migration_import_records "
                    f"TO {MIGRATION_RUNTIME_USER}"
                )
            )
            conn.execute(
                text(
                    f"REVOKE DELETE ON content.migration_import_records "
                    f"FROM {MIGRATION_RUNTIME_USER}"
                )
            )
            conn.execute(
                text(
                    f"REVOKE INSERT, UPDATE, DELETE ON content.review_decisions "
                    f"FROM {MIGRATION_RUNTIME_USER}"
                )
            )
            conn.execute(
                text(
                    f"REVOKE INSERT, UPDATE, DELETE ON content.publications "
                    f"FROM {MIGRATION_RUNTIME_USER}"
                )
            )
            conn.execute(
                text(
                    f"GRANT EXECUTE ON FUNCTION content.current_tenant_id() "
                    f"TO {MIGRATION_RUNTIME_USER}"
                )
            )
            conn.execute(
                text(f"GRANT USAGE ON SCHEMA integration TO {MIGRATION_RUNTIME_USER}")
            )
            conn.execute(
                text(
                    f"GRANT INSERT ON integration.outbox_messages "
                    f"TO {MIGRATION_RUNTIME_USER}"
                )
            )
            conn.execute(
                text(
                    f"REVOKE SELECT, UPDATE, DELETE ON integration.outbox_messages "
                    f"FROM {MIGRATION_RUNTIME_USER}"
                )
            )
            conn.execute(
                text(
                    f"GRANT EXECUTE ON FUNCTION integration.current_tenant_id() "
                    f"TO {MIGRATION_RUNTIME_USER}"
                )
            )
            # TOS-DEV02: durable Teaching Work container (no DELETE, RLS enforced).
            conn.execute(text(f"GRANT USAGE ON SCHEMA teaching TO {RUNTIME_USER}"))
            conn.execute(
                text(
                    f"GRANT SELECT, INSERT, UPDATE ON teaching.works TO {RUNTIME_USER}"
                )
            )
            conn.execute(text(f"REVOKE DELETE ON teaching.works FROM {RUNTIME_USER}"))
            conn.execute(
                text(
                    f"GRANT SELECT, INSERT, UPDATE ON teaching.assignments "
                    f"TO {RUNTIME_USER}"
                )
            )
            conn.execute(
                text(f"REVOKE DELETE ON teaching.assignments FROM {RUNTIME_USER}")
            )
            # TOS-DEV07-I01 tables may be absent on intermediate Alembic revisions.
            has_executions = conn.execute(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM information_schema.tables
                        WHERE table_schema = 'teaching'
                          AND table_name = 'executions'
                    )
                    """
                )
            ).scalar_one()
            if has_executions:
                conn.execute(
                    text(
                        f"GRANT SELECT, INSERT, UPDATE ON teaching.executions "
                        f"TO {RUNTIME_USER}"
                    )
                )
                conn.execute(
                    text(f"REVOKE DELETE ON teaching.executions FROM {RUNTIME_USER}")
                )
                conn.execute(
                    text(
                        f"GRANT SELECT, INSERT ON teaching.execution_content_bindings "
                        f"TO {RUNTIME_USER}"
                    )
                )
                conn.execute(
                    text(
                        f"REVOKE UPDATE, DELETE ON teaching.execution_content_bindings "
                        f"FROM {RUNTIME_USER}"
                    )
                )
                conn.execute(
                    text(
                        f"GRANT SELECT, INSERT, UPDATE ON "
                        f"teaching.execution_observations TO {RUNTIME_USER}"
                    )
                )
                conn.execute(
                    text(
                        f"REVOKE DELETE ON teaching.execution_observations "
                        f"FROM {RUNTIME_USER}"
                    )
                )
            # TOS-DEV09-I01 immutable origin table may be absent on older heads.
            has_remediation_origins = conn.execute(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM information_schema.tables
                        WHERE table_schema = 'teaching'
                          AND table_name = 'work_remediation_origins'
                    )
                    """
                )
            ).scalar_one()
            if has_remediation_origins:
                conn.execute(
                    text(
                        f"GRANT SELECT, INSERT ON teaching.work_remediation_origins "
                        f"TO {RUNTIME_USER}"
                    )
                )
                conn.execute(
                    text(
                        f"REVOKE UPDATE, DELETE ON teaching.work_remediation_origins "
                        f"FROM {RUNTIME_USER}"
                    )
                )
            conn.execute(
                text(
                    f"GRANT EXECUTE ON FUNCTION teaching.current_tenant_id() "
                    f"TO {RUNTIME_USER}"
                )
            )
            # TOS-DEV08-I01 tables may be absent on intermediate Alembic revisions.
            has_assessment = conn.execute(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM information_schema.tables
                        WHERE table_schema = 'assessment'
                          AND table_name = 'classroom_assessments'
                    )
                    """
                )
            ).scalar_one()
            if has_assessment:
                conn.execute(
                    text(f"GRANT USAGE ON SCHEMA assessment TO {RUNTIME_USER}")
                )
                conn.execute(
                    text(
                        f"GRANT SELECT, INSERT, UPDATE ON "
                        f"assessment.classroom_assessments TO {RUNTIME_USER}"
                    )
                )
                conn.execute(
                    text(
                        f"REVOKE DELETE ON assessment.classroom_assessments "
                        f"FROM {RUNTIME_USER}"
                    )
                )
                conn.execute(
                    text(
                        f"GRANT EXECUTE ON FUNCTION assessment.current_tenant_id() "
                        f"TO {RUNTIME_USER}"
                    )
                )
            # TOS-DEV03: AI GenerationRun execution SoR (no DELETE, RLS enforced).
            conn.execute(text(f"GRANT USAGE ON SCHEMA ai TO {RUNTIME_USER}"))
            conn.execute(
                text(
                    f"GRANT SELECT, INSERT, UPDATE ON ai.generation_runs "
                    f"TO {RUNTIME_USER}"
                )
            )
            conn.execute(
                text(f"REVOKE DELETE ON ai.generation_runs FROM {RUNTIME_USER}")
            )
            conn.execute(
                text(
                    f"GRANT EXECUTE ON FUNCTION ai.current_tenant_id() "
                    f"TO {RUNTIME_USER}"
                )
            )
            conn.execute(text(f"GRANT USAGE ON SCHEMA api TO {RUNTIME_USER}"))
            conn.execute(
                text(
                    f"GRANT SELECT, INSERT ON api.idempotency_records TO {RUNTIME_USER}"
                )
            )
            conn.execute(
                text(
                    f"REVOKE UPDATE, DELETE ON api.idempotency_records FROM {RUNTIME_USER}"
                )
            )
            conn.execute(
                text(
                    f"GRANT EXECUTE ON FUNCTION api.current_tenant_id() TO {RUNTIME_USER}"
                )
            )
            conn.execute(text(f"GRANT USAGE ON SCHEMA workflow TO {RUNTIME_USER}"))
            conn.execute(
                text(
                    f"GRANT SELECT, INSERT ON workflow.workflow_start_intents "
                    f"TO {RUNTIME_USER}"
                )
            )
            conn.execute(
                text(
                    f"GRANT SELECT, INSERT ON workflow.workflow_command_intents "
                    f"TO {RUNTIME_USER}"
                )
            )
            conn.execute(
                text(
                    f"REVOKE UPDATE, DELETE ON workflow.workflow_start_intents "
                    f"FROM {RUNTIME_USER}"
                )
            )
            conn.execute(
                text(
                    f"REVOKE UPDATE, DELETE ON workflow.workflow_command_intents "
                    f"FROM {RUNTIME_USER}"
                )
            )
            conn.execute(
                text(
                    f"GRANT EXECUTE ON FUNCTION workflow.current_tenant_id() "
                    f"TO {RUNTIME_USER}"
                )
            )
            conn.execute(
                text(f"GRANT USAGE ON SCHEMA workflow TO {WORKFLOW_DISPATCHER_USER}")
            )
            conn.execute(
                text(
                    f"GRANT SELECT, UPDATE ON workflow.workflow_start_intents "
                    f"TO {WORKFLOW_DISPATCHER_USER}"
                )
            )
            conn.execute(
                text(
                    f"GRANT SELECT, UPDATE ON workflow.workflow_command_intents "
                    f"TO {WORKFLOW_DISPATCHER_USER}"
                )
            )
            conn.execute(
                text(
                    f"REVOKE INSERT, DELETE ON workflow.workflow_start_intents "
                    f"FROM {WORKFLOW_DISPATCHER_USER}"
                )
            )
            conn.execute(
                text(
                    f"REVOKE INSERT, DELETE ON workflow.workflow_command_intents "
                    f"FROM {WORKFLOW_DISPATCHER_USER}"
                )
            )
            conn.execute(
                text(
                    f"GRANT EXECUTE ON FUNCTION workflow.current_tenant_id() "
                    f"TO {WORKFLOW_DISPATCHER_USER}"
                )
            )
            conn.execute(text(f"GRANT USAGE ON SCHEMA integration TO {RUNTIME_USER}"))
            conn.execute(
                text(
                    f"GRANT INSERT ON integration.outbox_messages TO {RUNTIME_USER}"
                )
            )
            conn.execute(
                text(
                    f"REVOKE SELECT, UPDATE, DELETE ON integration.outbox_messages "
                    f"FROM {RUNTIME_USER}"
                )
            )
            conn.execute(
                text(
                    f"GRANT EXECUTE ON FUNCTION integration.current_tenant_id() "
                    f"TO {RUNTIME_USER}"
                )
            )
            conn.execute(
                text(f"GRANT USAGE ON SCHEMA integration TO {EVENT_DISPATCHER_USER}")
            )
            conn.execute(
                text(
                    f"GRANT SELECT ON integration.outbox_messages "
                    f"TO {EVENT_DISPATCHER_USER}"
                )
            )
            conn.execute(
                text(
                    f"GRANT UPDATE ("
                    f"status, attempt_count, available_at, claimed_by, claimed_until, "
                    f"published_at, broker_stream, broker_sequence, last_error_code"
                    f") ON integration.outbox_messages TO {EVENT_DISPATCHER_USER}"
                )
            )
            conn.execute(
                text(
                    f"REVOKE INSERT, DELETE ON integration.outbox_messages "
                    f"FROM {EVENT_DISPATCHER_USER}"
                )
            )
            conn.execute(
                text(
                    f"GRANT EXECUTE ON FUNCTION integration.current_tenant_id() "
                    f"TO {EVENT_DISPATCHER_USER}"
                )
            )
            # ADR-AIEOS-045: belt-and-suspenders EXECUTE on candidate discovery functions
            # (migration already grants matching dispatcher EXECUTE; PUBLIC remains revoked).
            conn.execute(
                text(
                    "GRANT EXECUTE ON FUNCTION "
                    "integration.list_outbox_dispatch_candidates(integer, timestamptz) "
                    f"TO {EVENT_DISPATCHER_USER}"
                )
            )
            conn.execute(
                text(
                    "GRANT EXECUTE ON FUNCTION "
                    "workflow.list_start_intent_candidates(integer, timestamptz) "
                    f"TO {WORKFLOW_DISPATCHER_USER}"
                )
            )
            conn.execute(
                text(
                    "GRANT EXECUTE ON FUNCTION "
                    "workflow.list_command_intent_candidates(integer, timestamptz) "
                    f"TO {WORKFLOW_DISPATCHER_USER}"
                )
            )
            # SAI-I02: INSERT-only security audit ledger (no SELECT/UPDATE/DELETE).
            conn.execute(text(f"GRANT USAGE ON SCHEMA security TO {RUNTIME_USER}"))
            conn.execute(
                text(f"GRANT INSERT ON security.audit_records TO {RUNTIME_USER}")
            )
            conn.execute(
                text(
                    f"REVOKE SELECT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER "
                    f"ON security.audit_records FROM {RUNTIME_USER}"
                )
            )
            # PED-I09: read-only current authority SoR (no grant/membership mutation).
            conn.execute(
                text(
                    f"GRANT SELECT ON security.principals, security.tenants, "
                    f"security.tenant_memberships, security.capability_grants "
                    f"TO {RUNTIME_USER}"
                )
            )
            conn.execute(
                text(
                    f"REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER "
                    f"ON security.principals, security.tenants, "
                    f"security.tenant_memberships, security.capability_grants "
                    f"FROM {RUNTIME_USER}"
                )
            )
            conn.execute(
                text(
                    f"GRANT EXECUTE ON FUNCTION security.current_tenant_id() "
                    f"TO {RUNTIME_USER}"
                )
            )
            conn.execute(
                text(
                    f"GRANT EXECUTE ON FUNCTION security.related_resource_refs_are_valid("
                    f"jsonb, text, uuid, bigint) TO {RUNTIME_USER}"
                )
            )
            # PED-I02: narrow migration-head metadata read for API readiness only.
            conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {RUNTIME_USER}"))
            conn.execute(
                text(f"GRANT SELECT ON TABLE public.alembic_version TO {RUNTIME_USER}")
            )
            conn.execute(
                text(
                    f"REVOKE INSERT, UPDATE, DELETE, TRUNCATE "
                    f"ON TABLE public.alembic_version FROM {RUNTIME_USER}"
                )
            )
            conn.execute(
                text(f"GRANT USAGE ON SCHEMA security TO {MIGRATION_RUNTIME_USER}")
            )
            conn.execute(
                text(
                    f"GRANT INSERT ON security.audit_records TO {MIGRATION_RUNTIME_USER}"
                )
            )
            conn.execute(
                text(
                    f"REVOKE SELECT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER "
                    f"ON security.audit_records FROM {MIGRATION_RUNTIME_USER}"
                )
            )
            conn.execute(
                text(
                    f"GRANT EXECUTE ON FUNCTION security.current_tenant_id() "
                    f"TO {MIGRATION_RUNTIME_USER}"
                )
            )
            conn.execute(
                text(
                    f"GRANT EXECUTE ON FUNCTION security.related_resource_refs_are_valid("
                    f"jsonb, text, uuid, bigint) TO {MIGRATION_RUNTIME_USER}"
                )
            )
            # PED-I10B2: test-only Asset SoR privileges (not a production grant).
            conn.execute(text(f"GRANT USAGE ON SCHEMA asset TO {RUNTIME_USER}"))
            conn.execute(
                text(
                    f"GRANT EXECUTE ON FUNCTION asset.current_tenant_id() "
                    f"TO {RUNTIME_USER}"
                )
            )
            conn.execute(
                text(f"GRANT SELECT, INSERT, UPDATE ON asset.assets TO {RUNTIME_USER}")
            )
            conn.execute(text(f"REVOKE DELETE ON asset.assets FROM {RUNTIME_USER}"))
            conn.execute(
                text(
                    f"GRANT SELECT, INSERT ON asset.asset_revisions TO {RUNTIME_USER}"
                )
            )
            conn.execute(
                text(
                    f"REVOKE UPDATE, DELETE ON asset.asset_revisions "
                    f"FROM {RUNTIME_USER}"
                )
            )
            conn.execute(
                text(
                    f"GRANT SELECT, INSERT, UPDATE ON asset.asset_revision_states "
                    f"TO {RUNTIME_USER}"
                )
            )
            conn.execute(
                text(
                    f"REVOKE DELETE ON asset.asset_revision_states FROM {RUNTIME_USER}"
                )
            )
            conn.execute(
                text(
                    f"GRANT SELECT, INSERT ON asset.deletion_evidence TO {RUNTIME_USER}"
                )
            )
            conn.execute(
                text(
                    f"REVOKE UPDATE, DELETE ON asset.deletion_evidence "
                    f"FROM {RUNTIME_USER}"
                )
            )


@pytest.fixture(scope="session")
def postgres18() -> Iterator[dict[str, str]]:
    external = os.environ.get("AIEOS_TEST_DATABASE_URL")
    started_container = False
    if external:
        b_url = os.environ.get("AIEOS_TEST_BOOTSTRAP_DATABASE_URL", external)
        m_url = external
        r_url = os.environ.get("AIEOS_TEST_RUNTIME_DATABASE_URL", external)
        mig_url = os.environ.get("AIEOS_TEST_MIGRATION_RUNTIME_DATABASE_URL", r_url)
        d_url = os.environ.get("AIEOS_TEST_WORKFLOW_DISPATCHER_DATABASE_URL", r_url)
        e_url = os.environ.get("AIEOS_TEST_EVENT_DISPATCHER_DATABASE_URL", r_url)
        port = HOST_PORT
    else:
        port = start_postgres()
        started_container = True
        b_url = bootstrap_url(port)
        m_url = migrator_url(port)
        r_url = runtime_url(port)
        mig_url = migration_runtime_url(port)
        d_url = workflow_dispatcher_url(port)
        e_url = event_dispatcher_url(port)

    bootstrap = wait_for_engine(b_url)
    with bootstrap.connect() as conn:
        version = conn.execute(text("SHOW server_version")).scalar_one()
        if not str(version).startswith("18."):
            raise RuntimeError(f"PostgreSQL 18 required; got {version}")
    provision_identities(bootstrap)
    os.environ["AIEOS_DATABASE_URL"] = m_url
    cfg = alembic_config(m_url)
    command.upgrade(cfg, "head")
    clear_asset_audit_rows_for_schema_downgrade(bootstrap)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    provision_runtime_grants(bootstrap)
    try:
        yield {
            "bootstrap_url": b_url,
            "migrator_url": m_url,
            "runtime_url": r_url,
            "migration_runtime_url": mig_url,
            "workflow_dispatcher_url": d_url,
            "event_dispatcher_url": e_url,
            "server_version": str(version),
            "port": port,
            "schema_owner_role": SCHEMA_OWNER_ROLE,
            "security_schema_owner_role": SECURITY_SCHEMA_OWNER_ROLE,
            "asset_schema_owner_role": ASSET_SCHEMA_OWNER_ROLE,
            "migrator_user": MIGRATOR_USER,
            "runtime_user": RUNTIME_USER,
            "migration_runtime_user": MIGRATION_RUNTIME_USER,
            "workflow_dispatcher_user": WORKFLOW_DISPATCHER_USER,
            "event_dispatcher_user": EVENT_DISPATCHER_USER,
        }
    finally:
        bootstrap.dispose()
        if started_container:
            subprocess.run(
                ["docker", "rm", "-f", CONTAINER_NAME],
                check=False,
                capture_output=True,
            )


@pytest.fixture(scope="session")
def bootstrap_engine(postgres18: dict[str, str]) -> Iterator[Engine]:
    engine = create_engine(postgres18["bootstrap_url"])
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def migrator_engine(postgres18: dict[str, str]) -> Iterator[Engine]:
    engine = create_engine(postgres18["migrator_url"])
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def runtime_engine(postgres18: dict[str, str]) -> Iterator[Engine]:
    engine = create_engine(postgres18["runtime_url"])
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def migration_runtime_engine(postgres18: dict[str, str]) -> Iterator[Engine]:
    engine = create_engine(postgres18["migration_runtime_url"])
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def workflow_dispatcher_engine(postgres18: dict[str, str]) -> Iterator[Engine]:
    engine = create_engine(postgres18["workflow_dispatcher_url"])
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def event_dispatcher_engine(postgres18: dict[str, str]) -> Iterator[Engine]:
    engine = create_engine(postgres18["event_dispatcher_url"])
    try:
        yield engine
    finally:
        engine.dispose()

"""Local PostgreSQL 18 substrate provisioning for developer tooling.

Reimplements the proven GCI-I02 test conventions without pytest coupling.
LOCAL DEVELOPMENT ONLY — NEVER PRODUCTION.
"""

from __future__ import annotations

import os
import time

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from tools.dev.constants import (
    ASSET_SCHEMA_OWNER_ROLE,
    BOOTSTRAP_USER,
    DB_NAME,
    DB_PASSWORD,
    EVENT_CANDIDATE_READER_ROLE,
    EVENT_DISPATCHER_USER,
    EXPECTED_ALEMBIC_HEAD,
    HOST,
    HOST_PORT,
    MIGRATION_RUNTIME_USER,
    MIGRATOR_USER,
    RUNTIME_USER,
    SCHEMA_OWNER_ROLE,
    SECURITY_SCHEMA_OWNER_ROLE,
    WORKFLOW_CANDIDATE_READER_ROLE,
    WORKFLOW_DISPATCHER_USER,
)


def repo_root():
    from pathlib import Path

    return Path(__file__).resolve().parents[2]


def validate_db_host(host: str) -> None:
    from tools.dev.constants import ALLOWED_DB_HOSTS, FORBIDDEN_DB_HOST_FRAGMENTS

    normalized = host.strip().lower()
    for fragment in FORBIDDEN_DB_HOST_FRAGMENTS:
        if fragment in normalized:
            raise ValueError(
                f"local database tooling refused production-like host {host!r}"
            )
    if normalized not in ALLOWED_DB_HOSTS:
        raise ValueError(
            f"local database host must be one of {sorted(ALLOWED_DB_HOSTS)!r}; got {host!r}"
        )


def bootstrap_url(port: str = HOST_PORT, host: str = HOST) -> str:
    validate_db_host(host)
    return (
        f"postgresql+psycopg://{BOOTSTRAP_USER}:{DB_PASSWORD}"
        f"@{host}:{port}/{DB_NAME}"
    )


def migrator_url(port: str = HOST_PORT, host: str = HOST) -> str:
    validate_db_host(host)
    return (
        f"postgresql+psycopg://{MIGRATOR_USER}:{DB_PASSWORD}"
        f"@{host}:{port}/{DB_NAME}"
    )


def runtime_url(port: str = HOST_PORT, host: str = HOST) -> str:
    validate_db_host(host)
    return (
        f"postgresql+psycopg://{RUNTIME_USER}:{DB_PASSWORD}"
        f"@{host}:{port}/{DB_NAME}"
    )


def alembic_config(url: str) -> Config:
    cfg = Config(str(repo_root() / "alembic.ini"))
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
            conn.execute(
                text(
                    f"GRANT EXECUTE ON FUNCTION teaching.current_tenant_id() "
                    f"TO {RUNTIME_USER}"
                )
            )
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


def apply_migrations() -> None:
    cfg = alembic_config(migrator_url())
    command.upgrade(cfg, "head")


def read_alembic_head(bootstrap: Engine) -> str | None:
    with bootstrap.connect() as conn:
        exists = conn.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = 'public' AND table_name = 'alembic_version'
                )
                """
            )
        ).scalar_one()
        if not exists:
            return None
        return conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()


def verify_alembic_head(bootstrap: Engine) -> str:
    head = read_alembic_head(bootstrap)
    if head != EXPECTED_ALEMBIC_HEAD:
        raise RuntimeError(
            f"alembic_version must be {EXPECTED_ALEMBIC_HEAD!r}; got {head!r}"
        )
    return head


def assert_postgres18(bootstrap: Engine) -> str:
    with bootstrap.connect() as conn:
        version = conn.execute(text("SHOW server_version")).scalar_one()
        if not str(version).startswith("18."):
            raise RuntimeError(f"PostgreSQL 18 required; got {version}")
        return str(version)


def provision_and_migrate() -> Engine:
    """Idempotent identity provisioning, migration, and runtime grants."""
    bootstrap = wait_for_engine(bootstrap_url())
    assert_postgres18(bootstrap)
    provision_identities(bootstrap)
    apply_migrations()
    provision_runtime_grants(bootstrap)
    verify_alembic_head(bootstrap)
    return bootstrap

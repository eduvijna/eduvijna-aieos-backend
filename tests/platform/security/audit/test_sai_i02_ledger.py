"""SAI-I02 PostgreSQL security audit ledger: RLS, immutability, repository."""

from __future__ import annotations

import ast
import io
import uuid
from contextlib import redirect_stdout
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError

from aieos.platform.events.models import MutationEventContext
from aieos.platform.resources import ResourceRef
from aieos.platform.security.audit import (
    AuditRecordId,
    SecurityAuditAction,
    SecurityAuditExecutionChannel,
    build_security_mutation_audit_record,
)
from aieos.platform.security.audit.persistence import (
    SecurityAuditPersistenceError,
    SqlAlchemySecurityMutationAuditRepository,
    audit_records_table,
)
from tests.conftest import (
    SCHEMA_OWNER_ROLE,
    SECURITY_SCHEMA_OWNER_ROLE,
    alembic_config,
    provision_runtime_grants,
)
from tests.dbutil import REPO_ROOT, clear_asset_audit_rows_for_schema_downgrade, set_tenant

pytestmark = pytest.mark.sai_i02

FIXED_NOW = datetime(2026, 8, 15, 6, 0, tzinfo=UTC)
VALID_TRACE = "b" * 31 + "2"
AUDIT_PERSISTENCE = (
    REPO_ROOT / "src" / "aieos" / "platform" / "security" / "audit" / "persistence"
)
CONTENT_ROOT = REPO_ROOT / "src" / "aieos" / "domains" / "content"
MIGRATIONS = REPO_ROOT / "migrations" / "versions"

COLUMNS = {
    "audit_record_id",
    "tenant_id",
    "action",
    "primary_resource_type",
    "primary_resource_id",
    "primary_resource_revision",
    "resource_revision_before",
    "resource_revision_after",
    "related_resource_refs",
    "initiating_principal_id",
    "effective_actor_id",
    "executing_principal_id",
    "delegation_id",
    "execution_channel",
    "correlation_id",
    "causation_id",
    "trace_id",
    "occurred_at",
}


def _event(**kwargs: uuid.UUID) -> MutationEventContext:
    return MutationEventContext(
        correlation_id=kwargs.get("correlation_id", uuid.uuid7()),
        causation_id=kwargs.get("causation_id", uuid.uuid7()),
        actor_principal_id=kwargs.get("actor_principal_id", uuid.uuid7()),
        effective_actor_id=kwargs.get("effective_actor_id", uuid.uuid7()),
    )


def _record(
    *,
    tenant_id: uuid.UUID | None = None,
    action: SecurityAuditAction = SecurityAuditAction.CONTENT_CREATE,
    before: int | None = None,
    after: int = 0,
    related: tuple[ResourceRef, ...] = (),
    event: MutationEventContext | None = None,
    executing: uuid.UUID | None = None,
    delegation: uuid.UUID | None = None,
    channel: SecurityAuditExecutionChannel = SecurityAuditExecutionChannel.API,
    trace_id: str | None = VALID_TRACE,
    audit_record_id: AuditRecordId | None = None,
):
    primary = ResourceRef("content.content", uuid.uuid7(), after)
    return build_security_mutation_audit_record(
        tenant_id=tenant_id or uuid.uuid7(),
        action=action,
        primary_resource_ref=primary,
        resource_revision_before=before,
        resource_revision_after=after,
        related_resource_refs=related,
        mutation_event_context=event or _event(),
        executing_principal_id=executing or uuid.uuid7(),
        execution_channel=channel,
        occurred_at=FIXED_NOW,
        delegation_id=delegation,
        trace_id=trace_id,
        audit_record_id=audit_record_id,
    )


def _count(bootstrap_engine, audit_record_id: uuid.UUID) -> int:
    with bootstrap_engine.connect() as conn:
        return int(
            conn.execute(
                text(
                    "SELECT count(*) FROM security.audit_records "
                    "WHERE audit_record_id = :id"
                ),
                {"id": audit_record_id},
            ).scalar_one()
        )


def _fetch(bootstrap_engine, audit_record_id: uuid.UUID) -> dict:
    with bootstrap_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT * FROM security.audit_records WHERE audit_record_id = :id"
            ),
            {"id": audit_record_id},
        ).mappings().one()
        return dict(row)


def _expect_insert_failure(conn, record) -> None:
    with pytest.raises(SecurityAuditPersistenceError):
        SqlAlchemySecurityMutationAuditRepository(conn).insert(record)


def _insert_raw(conn, **overrides):
    base = {
        "audit_record_id": uuid.uuid7(),
        "tenant_id": uuid.uuid7(),
        "action": "content.create",
        "primary_resource_type": "content.content",
        "primary_resource_id": uuid.uuid7(),
        "primary_resource_revision": 0,
        "resource_revision_before": None,
        "resource_revision_after": 0,
        "related_resource_refs": [],
        "initiating_principal_id": uuid.uuid7(),
        "effective_actor_id": uuid.uuid7(),
        "executing_principal_id": uuid.uuid7(),
        "delegation_id": None,
        "execution_channel": "API",
        "correlation_id": uuid.uuid7(),
        "causation_id": uuid.uuid7(),
        "trace_id": None,
        "occurred_at": FIXED_NOW,
    }
    base.update(overrides)
    conn.execute(
        text(
            """
            INSERT INTO security.audit_records (
                audit_record_id, tenant_id, action,
                primary_resource_type, primary_resource_id, primary_resource_revision,
                resource_revision_before, resource_revision_after,
                related_resource_refs,
                initiating_principal_id, effective_actor_id, executing_principal_id,
                delegation_id, execution_channel,
                correlation_id, causation_id, trace_id, occurred_at
            ) VALUES (
                :audit_record_id, :tenant_id, :action,
                :primary_resource_type, :primary_resource_id, :primary_resource_revision,
                :resource_revision_before, :resource_revision_after,
                CAST(:related_resource_refs AS jsonb),
                :initiating_principal_id, :effective_actor_id, :executing_principal_id,
                :delegation_id, :execution_channel,
                :correlation_id, :causation_id, :trace_id, :occurred_at
            )
            """
        ),
        {
            **base,
            "related_resource_refs": __import__("json").dumps(
                base["related_resource_refs"]
            ),
        },
    )


def _expect_raw_failure(conn, **overrides) -> None:
    with pytest.raises((IntegrityError, ProgrammingError, DBAPIError)):
        _insert_raw(conn, **overrides)
    conn.execute(text("ROLLBACK TO SAVEPOINT sai_i02_attempt"))
    conn.execute(text("SAVEPOINT sai_i02_attempt"))


class TestSaiI02SchemaAndRoles:
    def test_head_and_owner_separation(self, postgres18, bootstrap_engine) -> None:
        with bootstrap_engine.connect() as conn:
            assert (
                conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "tosd030002"
            )
            schema_owner = conn.execute(
                text(
                    "SELECT r.rolname FROM pg_namespace n "
                    "JOIN pg_roles r ON r.oid = n.nspowner "
                    "WHERE n.nspname = 'security'"
                )
            ).scalar_one()
            content_owner = conn.execute(
                text(
                    "SELECT r.rolname FROM pg_namespace n "
                    "JOIN pg_roles r ON r.oid = n.nspowner "
                    "WHERE n.nspname = 'content'"
                )
            ).scalar_one()
            assert schema_owner == SECURITY_SCHEMA_OWNER_ROLE
            assert content_owner == SCHEMA_OWNER_ROLE
            assert schema_owner != content_owner
            assert schema_owner != postgres18["migrator_user"]
            assert schema_owner != postgres18["runtime_user"]
            assert schema_owner != postgres18["migration_runtime_user"]

            attrs = conn.execute(
                text(
                    """
                    SELECT rolsuper, rolbypassrls, rolcreatedb, rolcreaterole, rolcanlogin
                    FROM pg_roles WHERE rolname = :role
                    """
                ),
                {"role": SECURITY_SCHEMA_OWNER_ROLE},
            ).one()
            assert attrs == (False, False, False, False, False)

            migrator = conn.execute(
                text(
                    """
                    SELECT rolsuper, rolbypassrls, rolinherit
                    FROM pg_roles WHERE rolname = :role
                    """
                ),
                {"role": postgres18["migrator_user"]},
            ).one()
            assert migrator == (False, False, False)

            for role in (
                postgres18["runtime_user"],
                postgres18["migration_runtime_user"],
            ):
                row = conn.execute(
                    text(
                        """
                        SELECT rolsuper, rolbypassrls
                        FROM pg_roles WHERE rolname = :role
                        """
                    ),
                    {"role": role},
                ).one()
                assert row == (False, False)

    def test_alembic_cycle_and_role_restore(self, postgres18, bootstrap_engine) -> None:
        cfg = alembic_config(postgres18["migrator_url"])
        clear_asset_audit_rows_for_schema_downgrade(bootstrap_engine)
        command.downgrade(cfg, "gcii130001")
        with bootstrap_engine.connect() as conn:
            assert (
                conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "gcii130001"
            )
            assert (
                conn.execute(
                    text(
                        "SELECT count(*) FROM pg_namespace WHERE nspname = 'security'"
                    )
                ).scalar_one()
                == 0
            )
        command.upgrade(cfg, "head")
        clear_asset_audit_rows_for_schema_downgrade(bootstrap_engine)
        command.downgrade(cfg, "base")
        command.upgrade(cfg, "head")
        provision_runtime_grants(bootstrap_engine)
        with bootstrap_engine.connect() as conn:
            assert (
                conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "tosd030002"
            )

    def test_offline_sql_role_order(self) -> None:
        cfg = alembic_config("postgresql+psycopg://offline-check/unused")
        output = io.StringIO()
        with redirect_stdout(output):
            command.upgrade(cfg, "base:head", sql=True)
        sql = output.getvalue()
        content_role = f"SET LOCAL ROLE {SCHEMA_OWNER_ROLE}"
        security_role = f"SET LOCAL ROLE {SECURITY_SCHEMA_OWNER_ROLE}"
        create_schema = "CREATE SCHEMA security"
        assert content_role in sql
        assert security_role in sql
        assert create_schema in sql
        assert sql.index(content_role) < sql.index(security_role)
        assert sql.index(security_role) < sql.index(create_schema)
        # Final restore to content owner after security DDL block.
        restore_at = sql.rfind(content_role)
        assert restore_at > sql.index(create_schema)
        assert "PASSWORD" not in sql.upper()
        assert "aieos_test" not in sql

    def test_no_foreign_keys(self, bootstrap_engine) -> None:
        with bootstrap_engine.connect() as conn:
            fks = conn.execute(
                text(
                    """
                    SELECT count(*) FROM information_schema.table_constraints
                    WHERE table_schema = 'security'
                      AND table_name = 'audit_records'
                      AND constraint_type = 'FOREIGN KEY'
                    """
                )
            ).scalar_one()
        assert int(fks) == 0

    def test_rls_enable_force_and_insert_only_policy(self, bootstrap_engine) -> None:
        with bootstrap_engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT c.relrowsecurity, c.relforcerowsecurity
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'security' AND c.relname = 'audit_records'
                    """
                )
            ).one()
            assert row == (True, True)
            policies = list(
                conn.execute(
                    text(
                        """
                        SELECT polname, polcmd
                        FROM pg_policy p
                        JOIN pg_class c ON c.oid = p.polrelid
                        JOIN pg_namespace n ON n.oid = c.relnamespace
                        WHERE n.nspname = 'security' AND c.relname = 'audit_records'
                        """
                    )
                )
            )
        assert policies == [("audit_records_tenant_insert", "a")]

    def test_model_parity(self, bootstrap_engine) -> None:
        insp = inspect(bootstrap_engine)
        physical = {col["name"] for col in insp.get_columns("audit_records", schema="security")}
        assert physical == COLUMNS
        assert set(audit_records_table.c.keys()) == COLUMNS
        assert audit_records_table.schema == "security"
        for col in insp.get_columns("audit_records", schema="security"):
            mapped = audit_records_table.c[col["name"]]
            assert mapped.nullable is col["nullable"]
        pk = insp.get_pk_constraint("audit_records", schema="security")
        assert pk["constrained_columns"] == ["audit_record_id"]


class TestSaiI02Repository:
    def test_canonical_insert_and_commit(self, runtime_engine, bootstrap_engine) -> None:
        actor = uuid.uuid7()
        effective = uuid.uuid7()
        executing = uuid.uuid7()
        delegation = uuid.uuid7()
        correlation = uuid.uuid7()
        causation = uuid.uuid7()
        tenant = uuid.uuid7()
        related = (
            ResourceRef("content.content_version", uuid.uuid7(), 1),
        )
        record = _record(
            tenant_id=tenant,
            related=related,
            event=_event(
                actor_principal_id=actor,
                effective_actor_id=effective,
                correlation_id=correlation,
                causation_id=causation,
            ),
            executing=executing,
            delegation=delegation,
            channel=SecurityAuditExecutionChannel.API,
            trace_id=VALID_TRACE,
        )
        with runtime_engine.connect() as conn:
            with conn.begin():
                set_tenant(conn, tenant)
                SqlAlchemySecurityMutationAuditRepository(conn).insert(record)
        row = _fetch(bootstrap_engine, record.audit_record_id.value)
        assert row["audit_record_id"] == record.audit_record_id.value
        assert row["tenant_id"] == tenant
        assert row["action"] == "content.create"
        assert row["primary_resource_type"] == record.primary_resource_ref.resource_type
        assert row["primary_resource_id"] == record.primary_resource_ref.resource_id
        assert row["primary_resource_revision"] == 0
        assert row["resource_revision_before"] is None
        assert row["resource_revision_after"] == 0
        assert row["related_resource_refs"] == [
            {
                "resource_type": related[0].resource_type,
                "resource_id": str(related[0].resource_id),
                "resource_revision": 1,
            }
        ]
        assert row["initiating_principal_id"] == actor
        assert row["effective_actor_id"] == effective
        assert row["executing_principal_id"] == executing
        assert row["delegation_id"] == delegation
        assert row["execution_channel"] == "API"
        assert row["correlation_id"] == correlation
        assert row["causation_id"] == causation
        assert row["trace_id"] == VALID_TRACE
        assert row["occurred_at"] == FIXED_NOW

    def test_caller_owned_rollback(self, runtime_engine, bootstrap_engine) -> None:
        record = _record()
        with runtime_engine.connect() as conn:
            trans = conn.begin()
            set_tenant(conn, record.tenant_id)
            SqlAlchemySecurityMutationAuditRepository(conn).insert(record)
            trans.rollback()
        assert _count(bootstrap_engine, record.audit_record_id.value) == 0

    def test_missing_tenant_fail_closed(self, runtime_engine, bootstrap_engine) -> None:
        record = _record()
        with runtime_engine.connect() as conn:
            with conn.begin():
                _expect_insert_failure(conn, record)
        assert _count(bootstrap_engine, record.audit_record_id.value) == 0

    def test_cross_tenant_insert_denied(self, runtime_engine, bootstrap_engine) -> None:
        record = _record(tenant_id=uuid.uuid7())
        with runtime_engine.connect() as conn:
            with conn.begin():
                set_tenant(conn, uuid.uuid7())
                _expect_insert_failure(conn, record)
        assert _count(bootstrap_engine, record.audit_record_id.value) == 0

    def test_pooled_tenant_context(self, runtime_engine, bootstrap_engine) -> None:
        tenant_a = uuid.uuid7()
        tenant_b = uuid.uuid7()
        record_a = _record(tenant_id=tenant_a)
        record_fail = _record(tenant_id=tenant_a)
        record_b = _record(tenant_id=tenant_b)
        with runtime_engine.connect() as conn:
            with conn.begin():
                set_tenant(conn, tenant_a)
                SqlAlchemySecurityMutationAuditRepository(conn).insert(record_a)
            with conn.begin():
                _expect_insert_failure(conn, record_fail)
            with conn.begin():
                set_tenant(conn, tenant_b)
                SqlAlchemySecurityMutationAuditRepository(conn).insert(record_b)
        assert _count(bootstrap_engine, record_a.audit_record_id.value) == 1
        assert _count(bootstrap_engine, record_fail.audit_record_id.value) == 0
        assert _count(bootstrap_engine, record_b.audit_record_id.value) == 1

    def test_repository_has_no_commit_rollback_or_reads(self) -> None:
        src = (
            AUDIT_PERSISTENCE / "repositories.py"
        ).read_text(encoding="utf-8")
        assert "commit(" not in src
        assert "rollback(" not in src
        assert "engine.begin" not in src
        assert "engine.connect" not in src
        tree = ast.parse(src)
        methods = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
        }
        assert methods == {"__init__", "insert", "_related_refs_as_json"}
        for forbidden in ("get", "list", "search", "find", "query", "count"):
            assert forbidden not in methods


class TestSaiI02Privileges:
    def test_runtime_select_update_delete_denied(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        record = _record()
        with runtime_engine.connect() as conn:
            with conn.begin():
                set_tenant(conn, record.tenant_id)
                SqlAlchemySecurityMutationAuditRepository(conn).insert(record)
        with runtime_engine.connect() as conn:
            with conn.begin():
                set_tenant(conn, record.tenant_id)
                with pytest.raises(ProgrammingError):
                    conn.execute(text("SELECT * FROM security.audit_records"))
        with runtime_engine.connect() as conn:
            with conn.begin():
                set_tenant(conn, record.tenant_id)
                with pytest.raises(ProgrammingError):
                    conn.execute(
                        text(
                            "UPDATE security.audit_records SET action = 'content.publish'"
                        )
                    )
        with runtime_engine.connect() as conn:
            with conn.begin():
                set_tenant(conn, record.tenant_id)
                with pytest.raises(ProgrammingError):
                    conn.execute(text("DELETE FROM security.audit_records"))

    def test_migration_runtime_insert_allowed_reads_denied(
        self, postgres18, bootstrap_engine
    ) -> None:
        engine = create_engine(postgres18["migration_runtime_url"])
        try:
            record = _record()
            with engine.connect() as conn:
                with conn.begin():
                    set_tenant(conn, record.tenant_id)
                    SqlAlchemySecurityMutationAuditRepository(conn).insert(record)
            assert _count(bootstrap_engine, record.audit_record_id.value) == 1
            bad = _record(tenant_id=uuid.uuid7())
            with engine.connect() as conn:
                with conn.begin():
                    set_tenant(conn, uuid.uuid7())
                    _expect_insert_failure(conn, bad)
            with engine.connect() as conn:
                with conn.begin():
                    with pytest.raises(ProgrammingError):
                        conn.execute(text("SELECT * FROM security.audit_records"))
            with engine.connect() as conn:
                with conn.begin():
                    with pytest.raises(ProgrammingError):
                        conn.execute(
                            text(
                                "UPDATE security.audit_records "
                                "SET action = 'content.publish'"
                            )
                        )
            with engine.connect() as conn:
                with conn.begin():
                    with pytest.raises(ProgrammingError):
                        conn.execute(text("DELETE FROM security.audit_records"))
        finally:
            engine.dispose()

    def test_immutability_trigger(self, runtime_engine, bootstrap_engine) -> None:
        record = _record()
        with runtime_engine.connect() as conn:
            with conn.begin():
                set_tenant(conn, record.tenant_id)
                SqlAlchemySecurityMutationAuditRepository(conn).insert(record)
        with bootstrap_engine.connect() as conn:
            with conn.begin():
                with pytest.raises((IntegrityError, DBAPIError)):
                    conn.execute(
                        text(
                            "UPDATE security.audit_records "
                            "SET action = 'content.publish' "
                            "WHERE audit_record_id = :id"
                        ),
                        {"id": record.audit_record_id.value},
                    )
                with pytest.raises((IntegrityError, DBAPIError)):
                    conn.execute(
                        text(
                            "DELETE FROM security.audit_records "
                            "WHERE audit_record_id = :id"
                        ),
                        {"id": record.audit_record_id.value},
                    )
        assert _count(bootstrap_engine, record.audit_record_id.value) == 1


class TestSaiI02DbDefenses:
    def test_unknown_and_archive_action_rejected(self, bootstrap_engine) -> None:
        with bootstrap_engine.connect() as conn:
            with conn.begin():
                conn.execute(text("SAVEPOINT sai_i02_attempt"))
                _expect_raw_failure(conn, action="content.unknown")
                _expect_raw_failure(conn, action="content.archive")

    def test_revision_bypass_rejected(self, bootstrap_engine) -> None:
        with bootstrap_engine.connect() as conn:
            with conn.begin():
                conn.execute(text("SAVEPOINT sai_i02_attempt"))
                _expect_raw_failure(
                    conn,
                    action="content.create",
                    resource_revision_before=0,
                    resource_revision_after=1,
                    primary_resource_revision=1,
                )
                _expect_raw_failure(
                    conn,
                    action="content.migration.import",
                    resource_revision_before=None,
                    resource_revision_after=0,
                    primary_resource_revision=0,
                )
                _expect_raw_failure(
                    conn,
                    action="content.publish",
                    resource_revision_before=None,
                    resource_revision_after=1,
                    primary_resource_revision=1,
                )
                _expect_raw_failure(
                    conn,
                    action="content.publish",
                    resource_revision_before=3,
                    resource_revision_after=5,
                    primary_resource_revision=5,
                )
                _expect_raw_failure(
                    conn,
                    action="content.publish",
                    resource_revision_before=3,
                    resource_revision_after=4,
                    primary_resource_revision=5,
                )
                _expect_raw_failure(
                    conn,
                    action="content.publish",
                    resource_revision_before=-1,
                    resource_revision_after=0,
                    primary_resource_revision=0,
                )

    def test_uuidv4_rejected_uuidv7_accepted(self, bootstrap_engine) -> None:
        with bootstrap_engine.connect() as conn:
            with conn.begin():
                conn.execute(text("SAVEPOINT sai_i02_attempt"))
                _expect_raw_failure(conn, audit_record_id=uuid.uuid4())
                _insert_raw(conn, audit_record_id=uuid.uuid7())

    def test_related_ref_bypass(self, bootstrap_engine) -> None:
        primary_id = uuid.uuid7()
        valid = [
            {
                "resource_type": "content.content_version",
                "resource_id": str(uuid.uuid7()),
                "resource_revision": 1,
            }
        ]
        with bootstrap_engine.connect() as conn:
            with conn.begin():
                conn.execute(text("SAVEPOINT sai_i02_attempt"))
                _insert_raw(conn, related_resource_refs=[])
                _insert_raw(conn, related_resource_refs=valid)
                _expect_raw_failure(conn, related_resource_refs={"nope": True})
                _expect_raw_failure(
                    conn,
                    related_resource_refs=[
                        {
                            "resource_type": f"content.v{i}",
                            "resource_id": str(uuid.uuid7()),
                            "resource_revision": i,
                        }
                        for i in range(17)
                    ],
                )
                _expect_raw_failure(
                    conn,
                    related_resource_refs=[
                        {
                            "resource_type": "content.content_version",
                            "resource_id": str(uuid.uuid7()),
                        }
                    ],
                )
                _expect_raw_failure(
                    conn,
                    related_resource_refs=[
                        {
                            "resource_type": "content.content_version",
                            "resource_id": str(uuid.uuid7()),
                            "resource_revision": 1,
                            "token": "secret",
                        }
                    ],
                )
                _expect_raw_failure(
                    conn,
                    related_resource_refs=[
                        {
                            "resource_type": "Bad Type",
                            "resource_id": str(uuid.uuid7()),
                            "resource_revision": 1,
                        }
                    ],
                )
                _expect_raw_failure(
                    conn,
                    related_resource_refs=[
                        {
                            "resource_type": "content.content_version",
                            "resource_id": "not-a-uuid",
                            "resource_revision": 1,
                        }
                    ],
                )
                _expect_raw_failure(
                    conn,
                    related_resource_refs=[
                        {
                            "resource_type": "content.content_version",
                            "resource_id": str(uuid.uuid7()),
                            "resource_revision": -1,
                        }
                    ],
                )
                _expect_raw_failure(
                    conn,
                    related_resource_refs=[
                        {
                            "resource_type": "content.content_version",
                            "resource_id": str(uuid.uuid7()),
                            "resource_revision": 1.5,
                        }
                    ],
                )
                _expect_raw_failure(
                    conn,
                    related_resource_refs=[
                        {
                            "resource_type": "content.content_version",
                            "resource_id": str(uuid.uuid7()),
                            "resource_revision": 1.0,
                        }
                    ],
                )
                _expect_raw_failure(
                    conn,
                    related_resource_refs=[
                        {
                            "resource_type": "content.content_version",
                            "resource_id": str(uuid.uuid7()),
                            "resource_revision": "1",
                        }
                    ],
                )
                _expect_raw_failure(
                    conn,
                    related_resource_refs=[
                        {
                            "resource_type": "content.content_version",
                            "resource_id": str(uuid.uuid7()),
                            "resource_revision": True,
                        }
                    ],
                )
                _expect_raw_failure(
                    conn,
                    related_resource_refs=[
                        {
                            "resource_type": "content.content_version",
                            "resource_id": str(uuid.uuid7()),
                            "resource_revision": False,
                        }
                    ],
                )
                _expect_raw_failure(
                    conn,
                    related_resource_refs=[
                        {
                            "resource_type": "content.content_version",
                            "resource_id": str(uuid.uuid7()),
                            "resource_revision": 10**30,
                        }
                    ],
                )
                dup = {
                    "resource_type": "content.content_version",
                    "resource_id": str(uuid.uuid7()),
                    "resource_revision": 1,
                }
                _expect_raw_failure(conn, related_resource_refs=[dup, dup])
                same_id = uuid.uuid7()
                _expect_raw_failure(
                    conn,
                    related_resource_refs=[
                        {
                            "resource_type": "content.content_version",
                            "resource_id": str(same_id),
                            "resource_revision": 1,
                        },
                        {
                            "resource_type": "content.content_version",
                            "resource_id": str(same_id).upper(),
                            "resource_revision": 1,
                        },
                    ],
                )
                _expect_raw_failure(
                    conn,
                    related_resource_refs=[
                        {
                            "resource_type": "content.content_version",
                            "resource_id": str(same_id),
                            "resource_revision": 2,
                        },
                        {
                            "resource_type": "content.content_version",
                            "resource_id": "{" + str(same_id) + "}",
                            "resource_revision": 2,
                        },
                    ],
                )
                _expect_raw_failure(
                    conn,
                    related_resource_refs=[
                        {
                            "resource_type": "content.content_version",
                            "resource_id": str(same_id),
                            "resource_revision": 3,
                        },
                        {
                            "resource_type": "content.content_version",
                            "resource_id": str(same_id).replace("-", ""),
                            "resource_revision": 3,
                        },
                    ],
                )
                _expect_raw_failure(
                    conn,
                    primary_resource_id=primary_id,
                    related_resource_refs=[
                        {
                            "resource_type": "content.content",
                            "resource_id": str(primary_id),
                            "resource_revision": 0,
                        }
                    ],
                )
                _expect_raw_failure(
                    conn,
                    primary_resource_id=primary_id,
                    related_resource_refs=[
                        {
                            "resource_type": "content.content",
                            "resource_id": str(primary_id).upper(),
                            "resource_revision": 0,
                        }
                    ],
                )
                _expect_raw_failure(
                    conn,
                    primary_resource_id=primary_id,
                    related_resource_refs=[
                        {
                            "resource_type": "content.content",
                            "resource_id": "{" + str(primary_id) + "}",
                            "resource_revision": 0,
                        }
                    ],
                )
                _insert_raw(
                    conn,
                    related_resource_refs=[
                        {
                            "resource_type": "content.content_version",
                            "resource_id": str(uuid.uuid7()),
                            "resource_revision": None,
                        }
                    ],
                )
                _insert_raw(
                    conn,
                    related_resource_refs=[
                        {
                            "resource_type": "content.content_version",
                            "resource_id": str(uuid.uuid7()),
                            "resource_revision": 0,
                        }
                    ],
                )
                _insert_raw(
                    conn,
                    related_resource_refs=[
                        {
                            "resource_type": "content.content_version",
                            "resource_id": str(uuid.uuid7()),
                            "resource_revision": 1,
                        }
                    ],
                )
                sixteen = [
                    {
                        "resource_type": "content.content_version",
                        "resource_id": str(uuid.uuid7()),
                        "resource_revision": i,
                    }
                    for i in range(16)
                ]
                _insert_raw(conn, related_resource_refs=sixteen)

    def test_trace_bypass(self, bootstrap_engine) -> None:
        with bootstrap_engine.connect() as conn:
            with conn.begin():
                conn.execute(text("SAVEPOINT sai_i02_attempt"))
                _insert_raw(conn, trace_id=None)
                _insert_raw(conn, trace_id=VALID_TRACE)
                _expect_raw_failure(conn, trace_id=VALID_TRACE.upper())
                _expect_raw_failure(conn, trace_id="abc")
                _expect_raw_failure(conn, trace_id="g" * 32)
                _expect_raw_failure(conn, trace_id="0" * 32)


class TestSaiI02Architecture:
    def test_no_create_all_in_audit_persistence(self) -> None:
        hits: list[str] = []
        for path in AUDIT_PERSISTENCE.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
                    continue
                if "create_all(" in line or "CREATE TABLE" in line or "CREATE SCHEMA" in line:
                    hits.append(f"{path.name}:{stripped}")
        assert hits == []

    def test_content_api_ai_migration_audit_wired_workflow_pending(self) -> None:
        """SAI-I03/I04: API+AI+migration audit wired; workflow-origin still N/A."""
        in_uow = (CONTENT_ROOT / "application" / "in_uow.py").read_text(encoding="utf-8")
        assert "insert_required_content_audit" in in_uow
        create = (CONTENT_ROOT / "application" / "create.py").read_text(encoding="utf-8")
        assert "create_content_in_uow" in create
        ai = (CONTENT_ROOT / "application" / "ai_materialization.py").read_text(
            encoding="utf-8"
        )
        migration = (CONTENT_ROOT / "application" / "migration_import.py").read_text(
            encoding="utf-8"
        )
        assert "materialize_ai_version_in_uow" in ai
        assert "insert_required_content_audit" in migration
        uow = (
            CONTENT_ROOT
            / "infrastructure"
            / "persistence"
            / "uow.py"
        ).read_text(encoding="utf-8")
        assert "ContentSecurityMutationAuditRepository" in uow
        assert "self.audit =" in uow
        ports = (CONTENT_ROOT / "application" / "ports.py").read_text(encoding="utf-8")
        assert "SecurityMutationAuditRepository" in ports
        assert "SqlAlchemySecurityMutationAuditRepository" not in ports
        assert "WORKFLOW_ACTIVITY" not in ai
        assert "WORKFLOW_ACTIVITY" not in migration

    def test_no_audit_http(self) -> None:
        routes = (
            CONTENT_ROOT / "api" / "v1" / "routes.py"
        ).read_text(encoding="utf-8")
        for needle in ("/audit", "audit_records", "SecurityMutationAudit"):
            assert needle not in routes

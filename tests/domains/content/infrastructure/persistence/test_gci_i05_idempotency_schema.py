"""GCI-I05 api.idempotency_records grants, RLS, ownership, and Alembic cycle."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.conftest import alembic_config, provision_runtime_grants
from tests.dbutil import REPO_ROOT, clear_asset_audit_rows_for_schema_downgrade, set_tenant

pytestmark = pytest.mark.gci_i05

FIXED_NOW = datetime(2026, 8, 13, 21, 0, tzinfo=UTC)


def _expect_dbapi(conn, fn, match: str) -> None:
    with pytest.raises(DBAPIError, match=match):
        with conn.begin_nested():
            fn()


def _insert_record(conn, *, tenant_id: uuid.UUID, principal_id: uuid.UUID) -> uuid.UUID:
    record_id = uuid.uuid7()
    conn.execute(
        text(
            """
            INSERT INTO api.idempotency_records (
                idempotency_record_id, tenant_id, actor_principal_id, operation,
                idempotency_key_sha256, request_fingerprint_sha256, result_content_id,
                result_version_id, result_aggregate_revision, created_at, expires_at
            ) VALUES (
                :rid, :tid, :pid, 'content_create.v1',
                :key, :fp, :cid, NULL, 0, :created, :expires
            )
            """
        ),
        {
            "rid": record_id,
            "tid": tenant_id,
            "pid": principal_id,
            "key": "a" * 64,
            "fp": "b" * 64,
            "cid": uuid.uuid7(),
            "created": FIXED_NOW,
            "expires": FIXED_NOW + timedelta(hours=24),
        },
    )
    return record_id


class TestIdempotencyPrivileges:
    def test_runtime_cannot_update_or_delete(self, runtime_engine, bootstrap_engine) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        with bootstrap_engine.begin() as conn:
            record_id = _insert_record(conn, tenant_id=tenant_id, principal_id=principal_id)
        with runtime_engine.connect() as conn:
            with conn.begin():
                set_tenant(conn, tenant_id)
                _expect_dbapi(
                    conn,
                    lambda: conn.execute(
                        text(
                            "UPDATE api.idempotency_records SET operation = 'x' "
                            "WHERE idempotency_record_id = :rid"
                        ),
                        {"rid": record_id},
                    ),
                    match="permission denied",
                )
                _expect_dbapi(
                    conn,
                    lambda: conn.execute(
                        text(
                            "DELETE FROM api.idempotency_records "
                            "WHERE idempotency_record_id = :rid"
                        ),
                        {"rid": record_id},
                    ),
                    match="permission denied",
                )

    def test_missing_tenant_context_fails_closed(self, runtime_engine, bootstrap_engine) -> None:
        tenant_id = uuid.uuid7()
        with bootstrap_engine.begin() as conn:
            _insert_record(conn, tenant_id=tenant_id, principal_id=uuid.uuid7())
        with runtime_engine.connect() as conn:
            with pytest.raises(DBAPIError):
                conn.execute(text("SELECT count(*) FROM api.idempotency_records")).scalar_one()

    def test_pooled_connection_does_not_leak_tenant(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_a = uuid.uuid7()
        tenant_b = uuid.uuid7()
        with bootstrap_engine.begin() as conn:
            _insert_record(conn, tenant_id=tenant_a, principal_id=uuid.uuid7())
        with runtime_engine.connect() as conn:
            with conn.begin():
                set_tenant(conn, tenant_a)
                count_a = conn.execute(
                    text("SELECT count(*) FROM api.idempotency_records")
                ).scalar_one()
            with conn.begin():
                set_tenant(conn, tenant_b)
                count_b = conn.execute(
                    text("SELECT count(*) FROM api.idempotency_records")
                ).scalar_one()
        assert int(count_a) == 1
        assert int(count_b) == 0

    def test_expires_at_is_after_created(self, bootstrap_engine) -> None:
        tenant_id = uuid.uuid7()
        with bootstrap_engine.begin() as conn:
            _insert_record(conn, tenant_id=tenant_id, principal_id=uuid.uuid7())
            row = conn.execute(
                text(
                    "SELECT created_at, expires_at FROM api.idempotency_records "
                    "WHERE tenant_id = :tid"
                ),
                {"tid": tenant_id},
            ).one()
        assert row.expires_at == row.created_at + timedelta(hours=24)


class TestAlembicCycle:
    def test_downgrade_gcii020001_and_reupgrade(
        self, postgres18, bootstrap_engine
    ) -> None:
        cfg = alembic_config(postgres18["migrator_url"])
        clear_asset_audit_rows_for_schema_downgrade(bootstrap_engine)
        command.downgrade(cfg, "gcii020001")
        with bootstrap_engine.connect() as conn:
            schemas = {
                row[0]
                for row in conn.execute(text("SELECT schema_name FROM information_schema.schemata"))
            }
            revision = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        assert "api" not in schemas
        assert revision == "gcii020001"
        command.upgrade(cfg, "head")
        clear_asset_audit_rows_for_schema_downgrade(bootstrap_engine)
        command.downgrade(cfg, "base")
        command.upgrade(cfg, "head")
        provision_runtime_grants(bootstrap_engine)
        with bootstrap_engine.connect() as conn:
            assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == ("pedi10b6001")
            api_tables = {
                row[0]
                for row in conn.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = 'api'")
                )
            }
        assert api_tables == {"idempotency_records"}
        versions = sorted(
            path.name
            for path in (REPO_ROOT / "migrations" / "versions").glob("*.py")
            if path.name != "__init__.py"
        )
        assert versions == [
            "gcii020001_content_schema.py",
            "gcii050001_api_idempotency.py",
            "gcii060001_review_decisions.py",
            "gcii070001_workflow_intents.py",
            "gcii080001_outbox_messages.py",
            "gcii090001_publications.py",
            "gcii100001_version_asset_refs.py",
            "gcii110001_ai_provenance.py",
            "gcii130001_migration_import.py",
            "pedi090001_security_authority.py",
            "pedi10b2001_asset_authority_sor.py",
    "pedi10b6001_asset_security_audit.py",
        "saii020001_security_audit_ledger.py",
        ]

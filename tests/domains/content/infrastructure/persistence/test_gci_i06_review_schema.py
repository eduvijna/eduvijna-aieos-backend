"""GCI-I06 content.review_decisions schema, RLS, immutability, and Alembic."""

from __future__ import annotations

import io
import uuid
from contextlib import redirect_stdout
from datetime import UTC, datetime

import pytest
from alembic import command
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from tests.conftest import SCHEMA_OWNER_ROLE, alembic_config, provision_runtime_grants
from tests.dbutil import REPO_ROOT, clear_asset_audit_rows_for_schema_downgrade, set_tenant

pytestmark = pytest.mark.gci_i06

SHA = "a" * 64
REVIEW_COLUMNS = {
    "review_decision_id",
    "tenant_id",
    "content_id",
    "version_id",
    "decision",
    "reason_code",
    "comment",
    "reviewer_principal_id",
    "effective_actor_id",
    "delegation_id",
    "decided_at",
    "correlation_id",
}


def _now() -> datetime:
    return datetime(2026, 8, 14, 12, 0, tzinfo=UTC)


def _expect_integrity(conn, thunk) -> None:
    with pytest.raises(IntegrityError):
        with conn.begin_nested():
            thunk()


def _expect_dbapi(conn, thunk, match: str | None = None) -> None:
    with pytest.raises(DBAPIError, match=match):
        with conn.begin_nested():
            thunk()


def _insert_content_version(conn, *, tenant_id: uuid.UUID | None = None):
    tenant_id = tenant_id or uuid.uuid7()
    content_id = uuid.uuid7()
    version_id = uuid.uuid7()
    owner = uuid.uuid7()
    conn.execute(
        text(
            """
            INSERT INTO content.contents (
                content_id, tenant_id, owner_principal_id, content_type, title,
                description, locale, stewardship_state, current_version_id,
                published_version_id, aggregate_revision, created_at,
                created_by_principal_id, updated_at, archived_at
            ) VALUES (
                :content_id, :tenant_id, :owner, 'test.generic', 'Title',
                'Description', 'en-IN', 'GENERATED', NULL,
                NULL, 1, :created_at, :owner, :created_at, NULL
            )
            """
        ),
        {
            "content_id": content_id,
            "tenant_id": tenant_id,
            "owner": owner,
            "created_at": _now(),
        },
    )
    conn.execute(
        text(
            """
            INSERT INTO content.content_versions (
                version_id, tenant_id, content_id, version_number, parent_version_id,
                schema_id, schema_version, payload, payload_sha256, origin,
                provenance, created_at, created_by_principal_id
            ) VALUES (
                :version_id, :tenant_id, :content_id, 1, NULL,
                'test.generic', 1, '{"marker":"v"}'::jsonb, :sha, 'HUMAN',
                NULL, :created_at, :owner
            )
            """
        ),
        {
            "version_id": version_id,
            "tenant_id": tenant_id,
            "content_id": content_id,
            "sha": SHA,
            "created_at": _now(),
            "owner": owner,
        },
    )
    conn.execute(
        text(
            "UPDATE content.contents SET current_version_id = :vid WHERE content_id = :cid"
        ),
        {"vid": version_id, "cid": content_id},
    )
    return tenant_id, content_id, version_id


def _insert_decision(
    conn,
    *,
    tenant_id: uuid.UUID,
    content_id: uuid.UUID,
    version_id: uuid.UUID,
    decision: str = "APPROVE",
    comment: str | None = None,
    reason_code: str | None = None,
):
    review_decision_id = uuid.uuid7()
    principal = uuid.uuid7()
    conn.execute(
        text(
            """
            INSERT INTO content.review_decisions (
                review_decision_id, tenant_id, content_id, version_id, decision,
                reason_code, comment, reviewer_principal_id, effective_actor_id,
                delegation_id, decided_at, correlation_id
            ) VALUES (
                :rid, :tid, :cid, :vid, :decision,
                :reason_code, :comment, :pid, :pid, NULL, :decided_at, :corr
            )
            """
        ),
        {
            "rid": review_decision_id,
            "tid": tenant_id,
            "cid": content_id,
            "vid": version_id,
            "decision": decision,
            "reason_code": reason_code,
            "comment": comment,
            "pid": principal,
            "decided_at": _now(),
            "corr": uuid.uuid7(),
        },
    )
    return review_decision_id


class TestReviewDecisionCatalog:
    def test_exact_physical_columns(self, bootstrap_engine) -> None:
        with bootstrap_engine.connect() as conn:
            columns = {
                row[0]
                for row in conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = 'content' AND table_name = 'review_decisions'"
                    )
                )
            }
            idemp = {
                row[0]
                for row in conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = 'api' AND table_name = 'idempotency_records'"
                    )
                )
            }
        assert columns == REVIEW_COLUMNS
        assert "result_review_decision_id" in idemp
        fks = inspect(bootstrap_engine).get_foreign_keys(
            "idempotency_records", schema="api"
        )
        assert all(
            "result_review_decision_id" not in (fk.get("constrained_columns") or [])
            for fk in fks
        )

    def test_decision_check_unique_and_version_fk(self, bootstrap_engine) -> None:
        with bootstrap_engine.begin() as conn:
            tenant_id, content_id, version_id = _insert_content_version(conn)
            _expect_integrity(
                conn,
                lambda: _insert_decision(
                    conn,
                    tenant_id=tenant_id,
                    content_id=content_id,
                    version_id=version_id,
                    decision="PENDING",
                ),
            )
            _insert_decision(
                conn,
                tenant_id=tenant_id,
                content_id=content_id,
                version_id=version_id,
                decision="APPROVE",
            )
            _expect_integrity(
                conn,
                lambda: _insert_decision(
                    conn,
                    tenant_id=tenant_id,
                    content_id=content_id,
                    version_id=version_id,
                    decision="REJECT",
                    comment="no",
                ),
            )
            _expect_integrity(
                conn,
                lambda: _insert_decision(
                    conn,
                    tenant_id=tenant_id,
                    content_id=content_id,
                    version_id=uuid.uuid7(),
                    decision="APPROVE",
                ),
            )


class TestReviewRlsAndImmutability:
    def test_rls_enable_and_force(self, bootstrap_engine) -> None:
        with bootstrap_engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT c.relrowsecurity, c.relforcerowsecurity
                    FROM pg_catalog.pg_class c
                    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'content' AND c.relname = 'review_decisions'
                    """
                )
            ).one()
        assert row.relrowsecurity is True
        assert row.relforcerowsecurity is True

    def test_missing_tenant_context_fails_closed(self, runtime_engine) -> None:
        with runtime_engine.connect() as conn:
            with conn.begin():
                _expect_dbapi(
                    conn,
                    lambda: conn.execute(
                        text("SELECT review_decision_id FROM content.review_decisions")
                    ).fetchall(),
                    match="aieos.tenant_id is not set",
                )

    def test_tenant_isolation(self, runtime_engine, bootstrap_engine) -> None:
        tenant_a = uuid.uuid7()
        tenant_b = uuid.uuid7()
        with bootstrap_engine.begin() as conn:
            _, content_a, version_a = _insert_content_version(conn, tenant_id=tenant_a)
            _, content_b, version_b = _insert_content_version(conn, tenant_id=tenant_b)
            id_a = _insert_decision(
                conn, tenant_id=tenant_a, content_id=content_a, version_id=version_a
            )
            id_b = _insert_decision(
                conn, tenant_id=tenant_b, content_id=content_b, version_id=version_b
            )
        with runtime_engine.connect() as conn:
            with conn.begin():
                set_tenant(conn, tenant_a)
                ids = {
                    row[0]
                    for row in conn.execute(
                        text("SELECT review_decision_id FROM content.review_decisions")
                    )
                }
                assert id_a in ids
                assert id_b not in ids

    def test_runtime_select_insert_only(self, runtime_engine, bootstrap_engine) -> None:
        tenant_id = uuid.uuid7()
        with bootstrap_engine.begin() as conn:
            _, content_id, version_id = _insert_content_version(conn, tenant_id=tenant_id)
            review_id = _insert_decision(
                conn, tenant_id=tenant_id, content_id=content_id, version_id=version_id
            )
            _, content_insert, version_insert = _insert_content_version(
                conn, tenant_id=tenant_id
            )
        with runtime_engine.connect() as conn:
            with conn.begin():
                set_tenant(conn, tenant_id)
                found = conn.execute(
                    text(
                        "SELECT review_decision_id FROM content.review_decisions "
                        "WHERE review_decision_id = :rid"
                    ),
                    {"rid": review_id},
                ).scalar_one()
                assert found == review_id
                inserted = _insert_decision(
                    conn,
                    tenant_id=tenant_id,
                    content_id=content_insert,
                    version_id=version_insert,
                    decision="REJECT",
                    comment="no",
                )
                assert inserted is not None
        with runtime_engine.connect() as conn:
            with conn.begin():
                set_tenant(conn, tenant_id)
                found = conn.execute(
                    text(
                        "SELECT review_decision_id FROM content.review_decisions "
                        "WHERE review_decision_id = :rid"
                    ),
                    {"rid": review_id},
                ).scalar_one()
                assert found == review_id
                _expect_dbapi(
                    conn,
                    lambda: conn.execute(
                        text(
                            "UPDATE content.review_decisions SET comment = 'x' "
                            "WHERE review_decision_id = :rid"
                        ),
                        {"rid": review_id},
                    ),
                    match="permission denied",
                )
                _expect_dbapi(
                    conn,
                    lambda: conn.execute(
                        text(
                            "DELETE FROM content.review_decisions "
                            "WHERE review_decision_id = :rid"
                        ),
                        {"rid": review_id},
                    ),
                    match="permission denied",
                )

    def test_privileged_update_and_delete_blocked_by_trigger(
        self, bootstrap_engine
    ) -> None:
        with bootstrap_engine.begin() as conn:
            tenant_id, content_id, version_id = _insert_content_version(conn)
            review_id = _insert_decision(
                conn, tenant_id=tenant_id, content_id=content_id, version_id=version_id
            )
            _expect_dbapi(
                conn,
                lambda: conn.execute(
                    text(
                        "UPDATE content.review_decisions SET comment = 'x' "
                        "WHERE review_decision_id = :rid"
                    ),
                    {"rid": review_id},
                ),
                match="content.review_decisions is immutable",
            )
        with bootstrap_engine.begin() as conn:
            tenant_id, content_id, version_id = _insert_content_version(conn)
            review_id = _insert_decision(
                conn, tenant_id=tenant_id, content_id=content_id, version_id=version_id
            )
            _expect_dbapi(
                conn,
                lambda: conn.execute(
                    text(
                        "DELETE FROM content.review_decisions "
                        "WHERE review_decision_id = :rid"
                    ),
                    {"rid": review_id},
                ),
                match="content.review_decisions is immutable",
            )

    def test_schema_owner_distinct_from_migrator_and_runtime(
        self, bootstrap_engine, postgres18
    ) -> None:
        owner = postgres18["schema_owner_role"]
        migrator = postgres18["migrator_user"]
        runtime = postgres18["runtime_user"]
        with bootstrap_engine.connect() as conn:
            table_owner = conn.execute(
                text(
                    """
                    SELECT pg_catalog.pg_get_userbyid(c.relowner)
                    FROM pg_catalog.pg_class c
                    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'content' AND c.relname = 'review_decisions'
                    """
                )
            ).scalar_one()
            fn_owner = conn.execute(
                text(
                    """
                    SELECT pg_catalog.pg_get_userbyid(p.proowner)
                    FROM pg_catalog.pg_proc p
                    JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
                    WHERE n.nspname = 'content'
                      AND p.proname = 'reject_review_decision_mutation'
                    """
                )
            ).scalar_one()
        assert table_owner == owner
        assert fn_owner == owner
        assert owner != migrator
        assert runtime != owner
        assert runtime != migrator


class TestAlembicCycleAndOfflineSql:
    def test_online_upgrade_downgrade_upgrade(self, postgres18, bootstrap_engine) -> None:
        cfg = alembic_config(postgres18["migrator_url"])
        clear_asset_audit_rows_for_schema_downgrade(bootstrap_engine)
        command.downgrade(cfg, "gcii050001")
        with bootstrap_engine.connect() as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = 'content'")
                )
            }
            revision = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            idemp_cols = {
                row[0]
                for row in conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = 'api' AND table_name = 'idempotency_records'"
                    )
                )
            }
        assert "review_decisions" not in tables
        assert "result_review_decision_id" not in idemp_cols
        assert revision == "gcii050001"
        command.upgrade(cfg, "head")
        clear_asset_audit_rows_for_schema_downgrade(bootstrap_engine)
        command.downgrade(cfg, "base")
        command.upgrade(cfg, "head")
        provision_runtime_grants(bootstrap_engine)
        with bootstrap_engine.connect() as conn:
            assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == ("tosd040001")
            tables = {
                row[0]
                for row in conn.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = 'content'")
                )
            }
        assert tables == {
            "contents",
            "content_versions",
            "review_decisions",
            "publications",
            "version_asset_refs",
            "migration_import_records",
        }

    def test_offline_sql_assumes_owner_before_i06_ddl(self, postgres18) -> None:
        cfg = alembic_config(postgres18["migrator_url"])
        output = io.StringIO()
        with redirect_stdout(output):
            command.upgrade(cfg, "base:head", sql=True)
        sql_text = output.getvalue()
        role_stmt = f"SET LOCAL ROLE {SCHEMA_OWNER_ROLE}"
        create_table = "CREATE TABLE content.review_decisions"
        add_column = "result_review_decision_id"
        role_at = sql_text.find(role_stmt)
        table_at = sql_text.find(create_table)
        column_at = sql_text.find(add_column)
        begin_at = sql_text.upper().find("BEGIN")
        assert role_at != -1, sql_text
        assert table_at != -1, sql_text
        assert column_at != -1, sql_text
        assert begin_at != -1, sql_text
        assert begin_at < role_at < table_at
        assert role_at < column_at
        versions = sorted(
            path.name
            for path in (REPO_ROOT / "migrations" / "versions").glob("*.py")
            if path.name != "__init__.py"
        )
        assert versions == [
            "adra045001_dispatcher_candidate_authority.py",
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
        "tosd020001_teaching_work.py",
        "tosd030001_generation_runs.py",
    "tosd030002_generation_run_work_fence.py",
    "tosd040001_multi_artifact_provenance_and_generation_fences.py",
        ]

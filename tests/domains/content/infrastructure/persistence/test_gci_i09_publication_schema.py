"""GCI-I09 content.publications schema, RLS, immutability, and Alembic."""

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
from tests.dbutil import REPO_ROOT, set_tenant

pytestmark = pytest.mark.gci_i09

SHA = "a" * 64
PUBLICATION_COLUMNS = {
    "publication_id",
    "tenant_id",
    "content_id",
    "version_id",
    "approval_decision_id",
    "published_by_principal_id",
    "effective_actor_id",
    "published_at",
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
                'Description', 'en-IN', 'APPROVED', NULL,
                NULL, 3, :created_at, :owner, :created_at, NULL
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
    decision_id = uuid.uuid7()
    principal = uuid.uuid7()
    conn.execute(
        text(
            """
            INSERT INTO content.review_decisions (
                review_decision_id, tenant_id, content_id, version_id, decision,
                reason_code, comment, reviewer_principal_id, effective_actor_id,
                delegation_id, decided_at, correlation_id
            ) VALUES (
                :rid, :tid, :cid, :vid, 'APPROVE',
                NULL, NULL, :pid, :pid, NULL, :decided_at, :corr
            )
            """
        ),
        {
            "rid": decision_id,
            "tid": tenant_id,
            "cid": content_id,
            "vid": version_id,
            "pid": principal,
            "decided_at": _now(),
            "corr": uuid.uuid7(),
        },
    )
    return tenant_id, content_id, version_id, decision_id


def _insert_publication(
    conn,
    *,
    tenant_id: uuid.UUID,
    content_id: uuid.UUID,
    version_id: uuid.UUID,
    approval_decision_id: uuid.UUID,
):
    publication_id = uuid.uuid7()
    principal = uuid.uuid7()
    conn.execute(
        text(
            """
            INSERT INTO content.publications (
                publication_id, tenant_id, content_id, version_id,
                approval_decision_id, published_by_principal_id, effective_actor_id,
                published_at, correlation_id
            ) VALUES (
                :pid, :tid, :cid, :vid,
                :decision, :principal, :principal, :published_at, :corr
            )
            """
        ),
        {
            "pid": publication_id,
            "tid": tenant_id,
            "cid": content_id,
            "vid": version_id,
            "decision": approval_decision_id,
            "principal": principal,
            "published_at": _now(),
            "corr": uuid.uuid7(),
        },
    )
    return publication_id


class TestPublicationCatalog:
    def test_exact_physical_columns_unique_and_fks(self, bootstrap_engine) -> None:
        with bootstrap_engine.connect() as conn:
            columns = {
                row[0]
                for row in conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = 'content' AND table_name = 'publications'"
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
        assert columns == PUBLICATION_COLUMNS
        assert "result_publication_id" in idemp
        insp = inspect(bootstrap_engine)
        fks = insp.get_foreign_keys("publications", schema="content")
        constrained = {
            tuple(fk.get("constrained_columns") or []) for fk in fks
        }
        assert ("tenant_id", "content_id", "version_id") in constrained or any(
            set(cols) == {"tenant_id", "content_id", "version_id"} for cols in constrained
        )
        assert any(
            (fk.get("constrained_columns") or []) == ["approval_decision_id"]
            for fk in fks
        )
        uniques = insp.get_unique_constraints("publications", schema="content")
        unique_cols = {tuple(u.get("column_names") or []) for u in uniques}
        assert ("tenant_id", "content_id", "version_id") in unique_cols
        idemp_fks = insp.get_foreign_keys("idempotency_records", schema="api")
        assert all(
            "result_publication_id" not in (fk.get("constrained_columns") or [])
            for fk in idemp_fks
        )

    def test_unique_tenant_content_version_and_version_fk(self, bootstrap_engine) -> None:
        with bootstrap_engine.begin() as conn:
            tenant_id, content_id, version_id, decision_id = _insert_content_version(conn)
            _insert_publication(
                conn,
                tenant_id=tenant_id,
                content_id=content_id,
                version_id=version_id,
                approval_decision_id=decision_id,
            )
            _expect_integrity(
                conn,
                lambda: _insert_publication(
                    conn,
                    tenant_id=tenant_id,
                    content_id=content_id,
                    version_id=version_id,
                    approval_decision_id=decision_id,
                ),
            )
            _expect_integrity(
                conn,
                lambda: _insert_publication(
                    conn,
                    tenant_id=tenant_id,
                    content_id=content_id,
                    version_id=uuid.uuid7(),
                    approval_decision_id=decision_id,
                ),
            )


class TestPublicationRlsAndImmutability:
    def test_rls_enable_and_force(self, bootstrap_engine) -> None:
        with bootstrap_engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT c.relrowsecurity, c.relforcerowsecurity
                    FROM pg_catalog.pg_class c
                    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'content' AND c.relname = 'publications'
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
                        text("SELECT publication_id FROM content.publications")
                    ).fetchall(),
                    match="aieos.tenant_id is not set",
                )

    def test_tenant_isolation(self, runtime_engine, bootstrap_engine) -> None:
        tenant_a = uuid.uuid7()
        tenant_b = uuid.uuid7()
        with bootstrap_engine.begin() as conn:
            _, content_a, version_a, decision_a = _insert_content_version(
                conn, tenant_id=tenant_a
            )
            _, content_b, version_b, decision_b = _insert_content_version(
                conn, tenant_id=tenant_b
            )
            id_a = _insert_publication(
                conn,
                tenant_id=tenant_a,
                content_id=content_a,
                version_id=version_a,
                approval_decision_id=decision_a,
            )
            id_b = _insert_publication(
                conn,
                tenant_id=tenant_b,
                content_id=content_b,
                version_id=version_b,
                approval_decision_id=decision_b,
            )
        with runtime_engine.connect() as conn:
            with conn.begin():
                set_tenant(conn, tenant_a)
                ids = {
                    row[0]
                    for row in conn.execute(
                        text("SELECT publication_id FROM content.publications")
                    )
                }
                assert id_a in ids
                assert id_b not in ids

    def test_runtime_select_insert_only(self, runtime_engine, bootstrap_engine) -> None:
        tenant_id = uuid.uuid7()
        with bootstrap_engine.begin() as conn:
            _, content_id, version_id, decision_id = _insert_content_version(
                conn, tenant_id=tenant_id
            )
            publication_id = _insert_publication(
                conn,
                tenant_id=tenant_id,
                content_id=content_id,
                version_id=version_id,
                approval_decision_id=decision_id,
            )
            _, content_insert, version_insert, decision_insert = _insert_content_version(
                conn, tenant_id=tenant_id
            )
        with runtime_engine.connect() as conn:
            with conn.begin():
                set_tenant(conn, tenant_id)
                found = conn.execute(
                    text(
                        "SELECT publication_id FROM content.publications "
                        "WHERE publication_id = :pid"
                    ),
                    {"pid": publication_id},
                ).scalar_one()
                assert found == publication_id
                inserted = _insert_publication(
                    conn,
                    tenant_id=tenant_id,
                    content_id=content_insert,
                    version_id=version_insert,
                    approval_decision_id=decision_insert,
                )
                assert inserted is not None
        with runtime_engine.connect() as conn:
            with conn.begin():
                set_tenant(conn, tenant_id)
                _expect_dbapi(
                    conn,
                    lambda: conn.execute(
                        text(
                            "UPDATE content.publications SET correlation_id = :corr "
                            "WHERE publication_id = :pid"
                        ),
                        {"corr": uuid.uuid7(), "pid": publication_id},
                    ),
                    match="permission denied",
                )
                _expect_dbapi(
                    conn,
                    lambda: conn.execute(
                        text(
                            "DELETE FROM content.publications "
                            "WHERE publication_id = :pid"
                        ),
                        {"pid": publication_id},
                    ),
                    match="permission denied",
                )

    def test_privileged_update_and_delete_blocked_by_trigger(
        self, bootstrap_engine
    ) -> None:
        with bootstrap_engine.begin() as conn:
            tenant_id, content_id, version_id, decision_id = _insert_content_version(conn)
            publication_id = _insert_publication(
                conn,
                tenant_id=tenant_id,
                content_id=content_id,
                version_id=version_id,
                approval_decision_id=decision_id,
            )
            _expect_dbapi(
                conn,
                lambda: conn.execute(
                    text(
                        "UPDATE content.publications SET correlation_id = :corr "
                        "WHERE publication_id = :pid"
                    ),
                    {"corr": uuid.uuid7(), "pid": publication_id},
                ),
                match="content.publications is immutable",
            )
        with bootstrap_engine.begin() as conn:
            tenant_id, content_id, version_id, decision_id = _insert_content_version(conn)
            publication_id = _insert_publication(
                conn,
                tenant_id=tenant_id,
                content_id=content_id,
                version_id=version_id,
                approval_decision_id=decision_id,
            )
            _expect_dbapi(
                conn,
                lambda: conn.execute(
                    text(
                        "DELETE FROM content.publications "
                        "WHERE publication_id = :pid"
                    ),
                    {"pid": publication_id},
                ),
                match="content.publications is immutable",
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
                    WHERE n.nspname = 'content' AND c.relname = 'publications'
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
                      AND p.proname = 'reject_publication_mutation'
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
        command.downgrade(cfg, "gcii080001")
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
        assert "publications" not in tables
        assert "result_publication_id" not in idemp_cols
        assert revision == "gcii080001"
        command.upgrade(cfg, "head")
        command.downgrade(cfg, "base")
        command.upgrade(cfg, "head")
        provision_runtime_grants(bootstrap_engine)
        with bootstrap_engine.connect() as conn:
            assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
                "gcii130001"
            )
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

    def test_offline_sql_assumes_owner_before_i09_ddl(self, postgres18) -> None:
        cfg = alembic_config(postgres18["migrator_url"])
        output = io.StringIO()
        with redirect_stdout(output):
            command.upgrade(cfg, "base:head", sql=True)
        sql_text = output.getvalue()
        role_stmt = f"SET LOCAL ROLE {SCHEMA_OWNER_ROLE}"
        create_table = "CREATE TABLE content.publications"
        add_column = "result_publication_id"
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
            "gcii020001_content_schema.py",
            "gcii050001_api_idempotency.py",
            "gcii060001_review_decisions.py",
            "gcii070001_workflow_intents.py",
            "gcii080001_outbox_messages.py",
            "gcii090001_publications.py",
            "gcii100001_version_asset_refs.py",
    "gcii110001_ai_provenance.py",
    "gcii130001_migration_import.py",
        ]

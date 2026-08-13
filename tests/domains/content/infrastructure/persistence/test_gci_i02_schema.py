"""GCI-I02 PostgreSQL 18 schema, RLS, and immutability tests."""

from __future__ import annotations

import ast
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from aieos.domains.content.infrastructure.persistence.models import (
    content_versions_table,
    contents_table,
)
from tests.dbutil import REPO_ROOT, set_tenant

pytestmark = pytest.mark.gci_i02

SHA = "a" * 64
SRC_ROOT = REPO_ROOT / "src"
DOMAIN_ROOT = SRC_ROOT / "aieos" / "domains" / "content" / "domain"
CONTENTS_COLUMNS = {
    "content_id",
    "tenant_id",
    "owner_principal_id",
    "content_type",
    "title",
    "description",
    "locale",
    "stewardship_state",
    "current_version_id",
    "published_version_id",
    "aggregate_revision",
    "created_at",
    "created_by_principal_id",
    "updated_at",
    "archived_at",
}
VERSION_COLUMNS = {
    "version_id",
    "tenant_id",
    "content_id",
    "version_number",
    "parent_version_id",
    "schema_id",
    "schema_version",
    "payload",
    "payload_sha256",
    "origin",
    "provenance",
    "created_at",
    "created_by_principal_id",
}


def _now() -> datetime:
    return datetime(2026, 8, 13, 12, 0, tzinfo=UTC)


def _ids() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    return uuid.uuid7(), uuid.uuid7(), uuid.uuid7()


def _insert_content(
    conn,
    *,
    tenant_id: uuid.UUID,
    content_id: uuid.UUID,
    stewardship_state: str = "DRAFT",
    current_version_id: uuid.UUID | None = None,
    published_version_id: uuid.UUID | None = None,
    archived_at: datetime | None = None,
    aggregate_revision: int = 0,
    content_type: str = "test.generic",
    title: str = "Title",
    locale: str = "en-IN",
) -> None:
    conn.execute(
        text(
            """
            INSERT INTO content.contents (
                content_id, tenant_id, owner_principal_id, content_type, title,
                description, locale, stewardship_state, current_version_id,
                published_version_id, aggregate_revision, created_at,
                created_by_principal_id, updated_at, archived_at
            ) VALUES (
                :content_id, :tenant_id, :owner, :content_type, :title,
                :description, :locale, :state, :current_version_id,
                :published_version_id, :revision, :created_at,
                :owner, :updated_at, :archived_at
            )
            """
        ),
        {
            "content_id": content_id,
            "tenant_id": tenant_id,
            "owner": uuid.uuid7(),
            "content_type": content_type,
            "title": title,
            "description": "Description",
            "locale": locale,
            "state": stewardship_state,
            "current_version_id": current_version_id,
            "published_version_id": published_version_id,
            "revision": aggregate_revision,
            "created_at": _now(),
            "updated_at": _now(),
            "archived_at": archived_at,
        },
    )


def _insert_version_json(
    conn,
    *,
    tenant_id: uuid.UUID,
    content_id: uuid.UUID,
    version_id: uuid.UUID,
    version_number: int,
    parent_version_id: uuid.UUID | None,
    origin: str = "HUMAN",
    payload_sql: str = "'{\"marker\":\"v\"}'::jsonb",
    payload_sha256: str = SHA,
    provenance_sql: str = "NULL",
    schema_version: int = 1,
) -> None:
    conn.execute(
        text(
            f"""
            INSERT INTO content.content_versions (
                version_id, tenant_id, content_id, version_number, parent_version_id,
                schema_id, schema_version, payload, payload_sha256, origin,
                provenance, created_at, created_by_principal_id
            ) VALUES (
                :version_id, :tenant_id, :content_id, :version_number, :parent_version_id,
                'test.generic', :schema_version, {payload_sql}, :payload_sha256, :origin,
                {provenance_sql}, :created_at, :created_by
            )
            """
        ),
        {
            "version_id": version_id,
            "tenant_id": tenant_id,
            "content_id": content_id,
            "version_number": version_number,
            "parent_version_id": parent_version_id,
            "schema_version": schema_version,
            "payload_sha256": payload_sha256,
            "origin": origin,
            "created_at": _now(),
            "created_by": uuid.uuid7(),
        },
    )


def _expect_integrity(conn, thunk) -> None:
    with pytest.raises(IntegrityError):
        with conn.begin_nested():
            thunk()


def _expect_dbapi(conn, thunk, match: str | None = None) -> None:
    with pytest.raises(DBAPIError, match=match):
        with conn.begin_nested():
            thunk()


def _seed_pair(conn, *, tenant_id: uuid.UUID | None = None):
    tenant_id = tenant_id or uuid.uuid7()
    content_id, version_id, _ = _ids()
    _insert_content(conn, tenant_id=tenant_id, content_id=content_id)
    _insert_version_json(
        conn,
        tenant_id=tenant_id,
        content_id=content_id,
        version_id=version_id,
        version_number=1,
        parent_version_id=None,
    )
    return tenant_id, content_id, version_id


def _table_columns(conn, table: str) -> set[str]:
    return {
        row[0]
        for row in conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'content' AND table_name = :table"
            ),
            {"table": table},
        )
    }


class TestAlembicAndCatalog:
    def test_schema_created_only_through_alembic_and_required_tables(
        self, migrator_engine, postgres18
    ) -> None:
        with migrator_engine.connect() as conn:
            schemas = {
                row[0]
                for row in conn.execute(
                    text("SELECT schema_name FROM information_schema.schemata")
                )
            }
            assert "content" in schemas
            tables = {
                row[0]
                for row in conn.execute(
                    text(
                        "SELECT tablename FROM pg_tables WHERE schemaname = 'content'"
                    )
                )
            }
            assert tables == {"contents", "content_versions"}
            revision = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            assert revision == "gcii020001"
            edu = conn.execute(
                text(
                    "SELECT COUNT(*) FROM information_schema.tables "
                    "WHERE table_schema = 'edu' AND table_name IN "
                    "('content', 'contents', 'content_versions')"
                )
            ).scalar_one()
            assert edu == 0
        create_all_hits = []
        for path in (SRC_ROOT / "aieos").rglob("*.py"):
            if "create_all" in path.read_text(encoding="utf-8"):
                create_all_hits.append(str(path.relative_to(SRC_ROOT)))
        assert create_all_hits == []
        assert postgres18["server_version"].startswith("18.")

    def test_alembic_downgrade_reupgrade_left_schema_at_head(
        self, postgres18, migrator_engine
    ) -> None:
        insp = inspect(migrator_engine)
        assert "content" in insp.get_schema_names()
        assert set(insp.get_table_names(schema="content")) == {
            "contents",
            "content_versions",
        }
        with migrator_engine.connect() as conn:
            assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
                "gcii020001"
            )

    def test_required_columns_and_sqlalchemy_mappings(self, migrator_engine) -> None:
        with migrator_engine.connect() as conn:
            assert _table_columns(conn, "contents") == CONTENTS_COLUMNS
            assert _table_columns(conn, "content_versions") == VERSION_COLUMNS
        assert set(contents_table.c.keys()) == CONTENTS_COLUMNS
        assert set(content_versions_table.c.keys()) == VERSION_COLUMNS
        assert contents_table.schema == "content"
        assert content_versions_table.schema == "content"


class TestConstraints:
    def test_stewardship_and_origin_vocabularies(self, migrator_engine) -> None:
        with migrator_engine.begin() as conn:
            tenant_id, content_id, _ = _ids()
            _expect_integrity(
                conn,
                lambda: _insert_content(
                    conn,
                    tenant_id=tenant_id,
                    content_id=content_id,
                    stewardship_state="PUBLISHED",
                ),
            )
        with migrator_engine.begin() as conn:
            tenant_id, content_id, version_id = _ids()
            _insert_content(conn, tenant_id=tenant_id, content_id=content_id)
            _expect_integrity(
                conn,
                lambda: _insert_version_json(
                    conn,
                    tenant_id=tenant_id,
                    content_id=content_id,
                    version_id=version_id,
                    version_number=1,
                    parent_version_id=None,
                    origin="MACHINE",
                ),
            )
        with migrator_engine.begin() as conn:
            tenant_id, content_id, _ = _ids()
            for state in ("DRAFT", "GENERATED", "IN_REVIEW", "APPROVED"):
                cid = uuid.uuid7()
                _insert_content(
                    conn,
                    tenant_id=tenant_id,
                    content_id=cid,
                    stewardship_state=state,
                )
            _insert_content(
                conn,
                tenant_id=tenant_id,
                content_id=uuid.uuid7(),
                stewardship_state="ARCHIVED",
                archived_at=_now(),
            )
        with migrator_engine.begin() as conn:
            for origin in ("HUMAN", "IMPORT", "SYSTEM"):
                tenant_id, content_id, version_id = _ids()
                _insert_content(conn, tenant_id=tenant_id, content_id=content_id)
                _insert_version_json(
                    conn,
                    tenant_id=tenant_id,
                    content_id=content_id,
                    version_id=version_id,
                    version_number=1,
                    parent_version_id=None,
                    origin=origin,
                )

    def test_aggregate_revision_and_version_number(self, migrator_engine) -> None:
        with migrator_engine.begin() as conn:
            tenant_id, content_id, _ = _ids()
            _expect_integrity(
                conn,
                lambda: _insert_content(
                    conn,
                    tenant_id=tenant_id,
                    content_id=content_id,
                    aggregate_revision=-1,
                ),
            )
        with migrator_engine.begin() as conn:
            tenant_id, content_id, version_id = _ids()
            _insert_content(conn, tenant_id=tenant_id, content_id=content_id)
            _expect_integrity(
                conn,
                lambda: _insert_version_json(
                    conn,
                    tenant_id=tenant_id,
                    content_id=content_id,
                    version_id=version_id,
                    version_number=0,
                    parent_version_id=None,
                ),
            )

    def test_first_version_lineage_both_directions(self, migrator_engine) -> None:
        with migrator_engine.begin() as conn:
            tenant_id, content_id, version_id = _ids()
            parent = uuid.uuid7()
            _insert_content(conn, tenant_id=tenant_id, content_id=content_id)
            _expect_integrity(
                conn,
                lambda: _insert_version_json(
                    conn,
                    tenant_id=tenant_id,
                    content_id=content_id,
                    version_id=version_id,
                    version_number=1,
                    parent_version_id=parent,
                ),
            )
        with migrator_engine.begin() as conn:
            tenant_id, content_id, version_id = _ids()
            _insert_content(conn, tenant_id=tenant_id, content_id=content_id)
            _expect_integrity(
                conn,
                lambda: _insert_version_json(
                    conn,
                    tenant_id=tenant_id,
                    content_id=content_id,
                    version_id=version_id,
                    version_number=2,
                    parent_version_id=None,
                ),
            )

    def test_duplicate_version_number_rejected(self, migrator_engine) -> None:
        with migrator_engine.begin() as conn:
            tenant_id, content_id, v1 = _ids()
            _insert_content(conn, tenant_id=tenant_id, content_id=content_id)
            _insert_version_json(
                conn,
                tenant_id=tenant_id,
                content_id=content_id,
                version_id=v1,
                version_number=1,
                parent_version_id=None,
            )
            _expect_integrity(
                conn,
                lambda: _insert_version_json(
                    conn,
                    tenant_id=tenant_id,
                    content_id=content_id,
                    version_id=uuid.uuid7(),
                    version_number=1,
                    parent_version_id=None,
                ),
            )

    def test_cross_tenant_and_cross_content_lineage(self, migrator_engine) -> None:
        with migrator_engine.begin() as conn:
            tenant_a, content_a, version_a = _seed_pair(conn)
            tenant_b = uuid.uuid7()
            content_b = uuid.uuid7()
            _insert_content(conn, tenant_id=tenant_b, content_id=content_b)
            _expect_integrity(
                conn,
                lambda: _insert_version_json(
                    conn,
                    tenant_id=tenant_a,
                    content_id=content_b,
                    version_id=uuid.uuid7(),
                    version_number=1,
                    parent_version_id=None,
                ),
            )
            _expect_integrity(
                conn,
                lambda: _insert_version_json(
                    conn,
                    tenant_id=tenant_b,
                    content_id=content_b,
                    version_id=uuid.uuid7(),
                    version_number=2,
                    parent_version_id=version_a,
                ),
            )
            content_c = uuid.uuid7()
            _insert_content(conn, tenant_id=tenant_a, content_id=content_c)
            _expect_integrity(
                conn,
                lambda: _insert_version_json(
                    conn,
                    tenant_id=tenant_a,
                    content_id=content_c,
                    version_id=uuid.uuid7(),
                    version_number=2,
                    parent_version_id=version_a,
                ),
            )

    def test_current_and_published_pointers_same_domain(self, migrator_engine) -> None:
        with pytest.raises(IntegrityError):
            with migrator_engine.begin() as conn:
                tenant_a, content_a, version_a = _seed_pair(conn)
                tenant_b, content_b, version_b = _seed_pair(conn)
                conn.execute(
                    text(
                        "UPDATE content.contents SET current_version_id = :vid "
                        "WHERE content_id = :cid"
                    ),
                    {"vid": version_b, "cid": content_a},
                )
        with pytest.raises(IntegrityError):
            with migrator_engine.begin() as conn:
                tenant_a, content_a, version_a = _seed_pair(conn)
                tenant_b, content_b, version_b = _seed_pair(conn)
                conn.execute(
                    text(
                        "UPDATE content.contents SET published_version_id = :vid "
                        "WHERE content_id = :cid"
                    ),
                    {"vid": version_b, "cid": content_a},
                )
        with migrator_engine.begin() as conn:
            tenant_a, content_a, version_a = _seed_pair(conn)
            conn.execute(
                text(
                    "UPDATE content.contents SET current_version_id = :vid "
                    "WHERE content_id = :cid"
                ),
                {"vid": version_a, "cid": content_a},
            )

    def test_archive_and_published_pointer_rules(self, migrator_engine) -> None:
        with migrator_engine.begin() as conn:
            tenant_id, content_id, v1 = _seed_pair(conn)
            v2 = uuid.uuid7()
            _insert_version_json(
                conn,
                tenant_id=tenant_id,
                content_id=content_id,
                version_id=v2,
                version_number=2,
                parent_version_id=v1,
            )
            conn.execute(
                text(
                    "UPDATE content.contents SET current_version_id = :current, "
                    "published_version_id = :published WHERE content_id = :cid"
                ),
                {"current": v2, "published": v1, "cid": content_id},
            )
            row = conn.execute(
                text(
                    "SELECT current_version_id, published_version_id FROM content.contents "
                    "WHERE content_id = :cid"
                ),
                {"cid": content_id},
            ).one()
            assert row[0] == v2
            assert row[1] == v1
            _expect_integrity(
                conn,
                lambda: conn.execute(
                    text(
                        "UPDATE content.contents SET stewardship_state = 'ARCHIVED', "
                        "archived_at = :archived WHERE content_id = :cid"
                    ),
                    {"archived": _now(), "cid": content_id},
                ),
            )
        with migrator_engine.begin() as conn:
            tenant_id, content_id, v1 = _seed_pair(conn)
            conn.execute(
                text(
                    "UPDATE content.contents SET stewardship_state = 'ARCHIVED', "
                    "archived_at = :archived, published_version_id = NULL, "
                    "current_version_id = :vid WHERE content_id = :cid"
                ),
                {"archived": _now(), "vid": v1, "cid": content_id},
            )

    def test_payload_sha_origin_provenance(self, migrator_engine) -> None:
        with migrator_engine.begin() as conn:
            tenant_id, content_id, version_id = _ids()
            _insert_content(conn, tenant_id=tenant_id, content_id=content_id)
            _expect_integrity(
                conn,
                lambda: _insert_version_json(
                    conn,
                    tenant_id=tenant_id,
                    content_id=content_id,
                    version_id=version_id,
                    version_number=1,
                    parent_version_id=None,
                    payload_sql="'[1]'::jsonb",
                ),
            )
        with migrator_engine.begin() as conn:
            tenant_id, content_id, version_id = _ids()
            _insert_content(conn, tenant_id=tenant_id, content_id=content_id)
            _expect_integrity(
                conn,
                lambda: _insert_version_json(
                    conn,
                    tenant_id=tenant_id,
                    content_id=content_id,
                    version_id=version_id,
                    version_number=1,
                    parent_version_id=None,
                    payload_sha256="ABC" + "a" * 61,
                ),
            )
        with migrator_engine.begin() as conn:
            tenant_id, content_id, version_id = _ids()
            _insert_content(conn, tenant_id=tenant_id, content_id=content_id)
            _expect_integrity(
                conn,
                lambda: _insert_version_json(
                    conn,
                    tenant_id=tenant_id,
                    content_id=content_id,
                    version_id=version_id,
                    version_number=1,
                    parent_version_id=None,
                    origin="AI",
                    provenance_sql="NULL",
                ),
            )
        with migrator_engine.begin() as conn:
            tenant_id, content_id, version_id = _ids()
            _insert_content(conn, tenant_id=tenant_id, content_id=content_id)
            _expect_integrity(
                conn,
                lambda: _insert_version_json(
                    conn,
                    tenant_id=tenant_id,
                    content_id=content_id,
                    version_id=version_id,
                    version_number=1,
                    parent_version_id=None,
                    origin="AI",
                    provenance_sql="'[1]'::jsonb",
                ),
            )
        with migrator_engine.begin() as conn:
            tenant_id, content_id, version_id = _ids()
            _insert_content(conn, tenant_id=tenant_id, content_id=content_id)
            _insert_version_json(
                conn,
                tenant_id=tenant_id,
                content_id=content_id,
                version_id=version_id,
                version_number=1,
                parent_version_id=None,
                origin="AI",
                provenance_sql="'{\"generator\":\"test\"}'::jsonb",
            )


class TestImmutability:
    def test_update_and_delete_rejected(self, migrator_engine) -> None:
        with migrator_engine.begin() as conn:
            tenant_id, content_id, version_id = _seed_pair(conn)
            _expect_dbapi(
                conn,
                lambda: conn.execute(
                    text(
                        "UPDATE content.content_versions SET origin = 'SYSTEM' "
                        "WHERE version_id = :vid"
                    ),
                    {"vid": version_id},
                ),
                match="immutable",
            )
        with migrator_engine.begin() as conn:
            tenant_id, content_id, version_id = _seed_pair(conn)
            _expect_dbapi(
                conn,
                lambda: conn.execute(
                    text("DELETE FROM content.content_versions WHERE version_id = :vid"),
                    {"vid": version_id},
                ),
                match="immutable",
            )


class TestRlsAndTenantContext:
    def test_tenant_isolation_and_insert_check(self, runtime_engine, migrator_engine) -> None:
        tenant_a = uuid.uuid7()
        tenant_b = uuid.uuid7()
        with migrator_engine.begin() as conn:
            _, content_a, _ = _seed_pair(conn, tenant_id=tenant_a)
            _, content_b, _ = _seed_pair(conn, tenant_id=tenant_b)
        with runtime_engine.connect() as conn:
            with conn.begin():
                set_tenant(conn, tenant_a)
                ids = {
                    row[0]
                    for row in conn.execute(text("SELECT content_id FROM content.contents"))
                }
                assert content_a in ids
                assert content_b not in ids
        with runtime_engine.connect() as conn:
            with conn.begin():
                set_tenant(conn, tenant_a)
                _expect_dbapi(
                    conn,
                    lambda: _insert_content(
                        conn,
                        tenant_id=tenant_b,
                        content_id=uuid.uuid7(),
                    ),
                )

    def test_missing_context_fails_closed(self, runtime_engine) -> None:
        with runtime_engine.connect() as conn:
            with conn.begin():
                _expect_dbapi(
                    conn,
                    lambda: conn.execute(
                        text("SELECT content_id FROM content.contents")
                    ).fetchall(),
                    match="aieos.tenant_id is not set",
                )

    def test_set_local_cleared_after_commit_and_rollback(
        self, runtime_engine, migrator_engine
    ) -> None:
        tenant_a = uuid.uuid7()
        with migrator_engine.begin() as conn:
            _seed_pair(conn, tenant_id=tenant_a)
        with runtime_engine.connect() as conn:
            with conn.begin():
                set_tenant(conn, tenant_a)
                assert conn.execute(text("SELECT count(*) FROM content.contents")).scalar_one() >= 1
            with conn.begin():
                _expect_dbapi(
                    conn,
                    lambda: conn.execute(
                        text("SELECT count(*) FROM content.contents")
                    ).scalar_one(),
                    match="aieos.tenant_id is not set",
                )
        with runtime_engine.connect() as conn:
            with conn.begin() as trans:
                set_tenant(conn, tenant_a)
                trans.rollback()
            with conn.begin():
                _expect_dbapi(
                    conn,
                    lambda: conn.execute(
                        text("SELECT count(*) FROM content.contents")
                    ).scalar_one(),
                    match="aieos.tenant_id is not set",
                )

    def test_reused_connection_does_not_leak_tenant(
        self, runtime_engine, migrator_engine
    ) -> None:
        tenant_a = uuid.uuid7()
        tenant_b = uuid.uuid7()
        with migrator_engine.begin() as conn:
            _, content_a, _ = _seed_pair(conn, tenant_id=tenant_a)
            _, content_b, _ = _seed_pair(conn, tenant_id=tenant_b)
        with runtime_engine.connect() as conn:
            with conn.begin():
                set_tenant(conn, tenant_a)
                ids = {
                    row[0]
                    for row in conn.execute(text("SELECT content_id FROM content.contents"))
                }
                assert content_a in ids
                assert content_b not in ids
            with conn.begin():
                set_tenant(conn, tenant_b)
                ids = {
                    row[0]
                    for row in conn.execute(text("SELECT content_id FROM content.contents"))
                }
                assert content_b in ids
                assert content_a not in ids


class TestArchitectureBoundary:
    def test_domain_package_has_no_persistence_imports(self) -> None:
        forbidden = ("sqlalchemy", "alembic", "psycopg", "psycopg2", "asyncpg")
        violations: list[str] = []
        for path in DOMAIN_ROOT.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.split(".")[0] in forbidden:
                            violations.append(f"{path.name}: import {alias.name}")
                elif isinstance(node, ast.ImportFrom) and node.module:
                    if node.module.split(".")[0] in forbidden:
                        violations.append(f"{path.name}: from {node.module}")
        assert violations == []

    def test_no_postgrest_or_legacy_or_later_slice_tables(self) -> None:
        hits: list[str] = []
        for path in (SRC_ROOT / "aieos").rglob("*.py"):
            text_src = path.read_text(encoding="utf-8")
            if "postgrest" in text_src.lower() or "edu.content" in text_src:
                hits.append(str(path.relative_to(SRC_ROOT)))
        assert hits == []
        for path in (REPO_ROOT / "migrations").rglob("*.py"):
            text_src = path.read_text(encoding="utf-8")
            for needle in ("review_decisions", "publications", "version_asset_refs"):
                if needle in text_src:
                    hits.append(f"{path.name}:{needle}")
        assert hits == []
        assert not Path(REPO_ROOT / "src" / "aieos" / "domains" / "content" / "application").exists()

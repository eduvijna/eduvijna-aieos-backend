"""GCI-I02 PostgreSQL 18 schema, RLS, and immutability tests."""

from __future__ import annotations

import ast
import io
import uuid
from contextlib import redirect_stdout
from datetime import UTC, datetime

import pytest
from alembic import command
from psycopg.pq import ExecStatus
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine.url import make_url
from sqlalchemy.exc import DBAPIError, IntegrityError

from aieos.domains.content.infrastructure.persistence.models import (
    content_versions_table,
    contents_table,
)
from tests.conftest import (
    ASSET_SCHEMA_OWNER_ROLE,
    SCHEMA_OWNER_ROLE,
    SECURITY_SCHEMA_OWNER_ROLE,
    alembic_config,
    provision_identities,
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
        self, bootstrap_engine, postgres18
    ) -> None:
        with bootstrap_engine.connect() as conn:
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
            assert tables == {
                "contents",
                "content_versions",
                "review_decisions",
                "publications",
                "version_asset_refs",
                "migration_import_records",
            }
            api_tables = {
                row[0]
                for row in conn.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = 'api'")
                )
            }
            assert "api" in schemas
            assert api_tables == {"idempotency_records"}
            assert "security" in schemas
            security_tables = {
                row[0]
                for row in conn.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = 'security'")
                )
            }
            assert security_tables == {
                "audit_records",
                "principals",
                "tenants",
                "tenant_memberships",
                "capability_grants",
            }
            revision = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            assert revision == "tosd040001"
            gcii02 = (
                REPO_ROOT / "migrations" / "versions" / "gcii020001_content_schema.py"
            ).read_text(encoding="utf-8")
            assert "CREATE SCHEMA content" in gcii02
            assert "CREATE SCHEMA api" not in gcii02
            assert "idempotency_records" not in gcii02
            assert "review_decisions" not in gcii02
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
        self, postgres18, bootstrap_engine
    ) -> None:
        insp = inspect(bootstrap_engine)
        assert "content" in insp.get_schema_names()
        assert set(insp.get_table_names(schema="content")) == {
            "contents",
            "content_versions",
            "review_decisions",
            "publications",
            "version_asset_refs",
            "migration_import_records",
        }
        assert "api" in insp.get_schema_names()
        assert set(insp.get_table_names(schema="api")) == {"idempotency_records"}
        with bootstrap_engine.connect() as conn:
            assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == ("tosd040001")
        assert "workflow" in insp.get_schema_names()
        assert set(insp.get_table_names(schema="workflow")) == {
            "workflow_start_intents",
            "workflow_command_intents",
        }
        assert "integration" in insp.get_schema_names()
        assert set(insp.get_table_names(schema="integration")) == {"outbox_messages"}
        assert "security" in insp.get_schema_names()
        assert set(insp.get_table_names(schema="security")) == {
            "audit_records",
            "principals",
            "tenants",
            "tenant_memberships",
            "capability_grants",
        }

    def test_required_columns_and_sqlalchemy_mappings(self, bootstrap_engine) -> None:
        with bootstrap_engine.connect() as conn:
            assert _table_columns(conn, "contents") == CONTENTS_COLUMNS
            assert _table_columns(conn, "content_versions") == VERSION_COLUMNS
        assert set(contents_table.c.keys()) == CONTENTS_COLUMNS
        assert set(content_versions_table.c.keys()) == VERSION_COLUMNS
        assert contents_table.schema == "content"
        assert content_versions_table.schema == "content"


class TestConstraints:
    def test_stewardship_and_origin_vocabularies(self, bootstrap_engine) -> None:
        with bootstrap_engine.begin() as conn:
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
        with bootstrap_engine.begin() as conn:
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
        with bootstrap_engine.begin() as conn:
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
        with bootstrap_engine.begin() as conn:
            for origin in ("HUMAN", "IMPORT", "SYSTEM"):
                tenant_id, content_id, version_id = _ids()
                _insert_content(conn, tenant_id=tenant_id, content_id=content_id)
                if origin == "IMPORT":
                    import json

                    prov = {
                        "kind": "migration_import",
                        "schema_version": 1,
                        "migration_batch_id": str(uuid.uuid7()),
                        "source_system": "legacy.edu",
                        "source_resource_type": "lesson",
                        "source_resource_id": "42",
                        "source_version": None,
                        "source_digest_sha256": "a" * 64,
                        "mapping_id": "edu.lesson.v1",
                        "mapping_version": 1,
                    }
                    provenance_sql = f"'{json.dumps(prov)}'::jsonb"
                else:
                    provenance_sql = "NULL"
                _insert_version_json(
                    conn,
                    tenant_id=tenant_id,
                    content_id=content_id,
                    version_id=version_id,
                    version_number=1,
                    parent_version_id=None,
                    origin=origin,
                    provenance_sql=provenance_sql,
                )

    def test_aggregate_revision_and_version_number(self, bootstrap_engine) -> None:
        with bootstrap_engine.begin() as conn:
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
        with bootstrap_engine.begin() as conn:
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

    def test_first_version_lineage_both_directions(self, bootstrap_engine) -> None:
        with bootstrap_engine.begin() as conn:
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
        with bootstrap_engine.begin() as conn:
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

    def test_duplicate_version_number_rejected(self, bootstrap_engine) -> None:
        with bootstrap_engine.begin() as conn:
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

    def test_cross_tenant_and_cross_content_lineage(self, bootstrap_engine) -> None:
        with bootstrap_engine.begin() as conn:
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

    def test_current_and_published_pointers_same_domain(self, bootstrap_engine) -> None:
        with pytest.raises(IntegrityError):
            with bootstrap_engine.begin() as conn:
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
            with bootstrap_engine.begin() as conn:
                tenant_a, content_a, version_a = _seed_pair(conn)
                tenant_b, content_b, version_b = _seed_pair(conn)
                conn.execute(
                    text(
                        "UPDATE content.contents SET published_version_id = :vid "
                        "WHERE content_id = :cid"
                    ),
                    {"vid": version_b, "cid": content_a},
                )
        with bootstrap_engine.begin() as conn:
            tenant_a, content_a, version_a = _seed_pair(conn)
            conn.execute(
                text(
                    "UPDATE content.contents SET current_version_id = :vid "
                    "WHERE content_id = :cid"
                ),
                {"vid": version_a, "cid": content_a},
            )

    def test_archive_and_published_pointer_rules(self, bootstrap_engine) -> None:
        with bootstrap_engine.begin() as conn:
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
        with bootstrap_engine.begin() as conn:
            tenant_id, content_id, v1 = _seed_pair(conn)
            conn.execute(
                text(
                    "UPDATE content.contents SET stewardship_state = 'ARCHIVED', "
                    "archived_at = :archived, published_version_id = NULL, "
                    "current_version_id = :vid WHERE content_id = :cid"
                ),
                {"archived": _now(), "vid": v1, "cid": content_id},
            )

    def test_payload_sha_origin_provenance(self, bootstrap_engine) -> None:
        with bootstrap_engine.begin() as conn:
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
        with bootstrap_engine.begin() as conn:
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
        with bootstrap_engine.begin() as conn:
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
        with bootstrap_engine.begin() as conn:
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
        with bootstrap_engine.begin() as conn:
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
                    provenance_sql="'{\"generator\":\"test\"}'::jsonb",
                ),
            )
        with bootstrap_engine.begin() as conn:
            import json

            tenant_id, content_id, version_id = _ids()
            _insert_content(conn, tenant_id=tenant_id, content_id=content_id)
            run_id = str(uuid.uuid7())
            correlation_id = str(uuid.uuid7())
            prov = json.dumps(
                {
                    "kind": "ai_generation",
                    "schema_version": 1,
                    "generation_run_ref": {
                        "resource_type": "generation.run",
                        "resource_id": run_id,
                        "resource_revision": None,
                    },
                    "prompt_execution_ref": None,
                    "provider_id": "test.provider",
                    "model_id": "neutral-model",
                    "capability_id": "content.generate.lesson",
                    "source_refs": [],
                    "policy_refs": [],
                    "evaluation_refs": [],
                    "correlation_id": correlation_id,
                }
            )
            _insert_version_json(
                conn,
                tenant_id=tenant_id,
                content_id=content_id,
                version_id=version_id,
                version_number=1,
                parent_version_id=None,
                origin="AI",
                provenance_sql=f"'{prov}'::jsonb",
            )


class TestImmutability:
    def test_privileged_identity_trigger_rejects_update_and_delete(
        self, bootstrap_engine
    ) -> None:
        with bootstrap_engine.begin() as conn:
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
        with bootstrap_engine.begin() as conn:
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
    def test_tenant_isolation_and_insert_check(self, runtime_engine, bootstrap_engine) -> None:
        tenant_a = uuid.uuid7()
        tenant_b = uuid.uuid7()
        with bootstrap_engine.begin() as conn:
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
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_a = uuid.uuid7()
        with bootstrap_engine.begin() as conn:
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
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_a = uuid.uuid7()
        tenant_b = uuid.uuid7()
        with bootstrap_engine.begin() as conn:
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


class TestPrivilegeAndOwnership:
    def test_schema_and_tables_owned_by_schema_owner_not_migrator_or_runtime(
        self, bootstrap_engine, migrator_engine, runtime_engine, postgres18
    ) -> None:
        owner = postgres18["schema_owner_role"]
        migrator = postgres18["migrator_user"]
        runtime = postgres18["runtime_user"]
        with bootstrap_engine.connect() as conn:
            schema_owner = conn.execute(
                text(
                    "SELECT pg_catalog.pg_get_userbyid(nspowner) "
                    "FROM pg_catalog.pg_namespace WHERE nspname = 'content'"
                )
            ).scalar_one()
            table_owners = dict(
                conn.execute(
                    text(
                        """
                        SELECT c.relname, pg_catalog.pg_get_userbyid(c.relowner)
                        FROM pg_catalog.pg_class c
                        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                        WHERE n.nspname = 'content' AND c.relkind = 'r'
                        """
                    )
                ).all()
            )
            roles = {
                row["rolname"]: row
                for row in conn.execute(
                    text(
                        """
                        SELECT rolname, rolsuper, rolbypassrls, rolcanlogin, rolinherit
                        FROM pg_catalog.pg_roles
                        WHERE rolname IN (:owner, :migrator, :runtime)
                        """
                    ),
                    {"owner": owner, "migrator": migrator, "runtime": runtime},
                ).mappings()
            }
        with bootstrap_engine.connect() as conn:
            api_schema_owner = conn.execute(
                text(
                    "SELECT pg_catalog.pg_get_userbyid(nspowner) "
                    "FROM pg_catalog.pg_namespace WHERE nspname = 'api'"
                )
            ).scalar_one()
            api_table_owners = dict(
                conn.execute(
                    text(
                        """
                        SELECT c.relname, pg_catalog.pg_get_userbyid(c.relowner)
                        FROM pg_catalog.pg_class c
                        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                        WHERE n.nspname = 'api' AND c.relkind = 'r'
                        """
                    )
                ).all()
            )
        assert schema_owner == owner
        assert table_owners["contents"] == owner
        assert table_owners["content_versions"] == owner
        assert table_owners["review_decisions"] == owner
        assert api_schema_owner == owner
        assert api_table_owners["idempotency_records"] == owner
        assert owner != migrator
        assert runtime != owner
        assert runtime != migrator
        assert roles[owner]["rolcanlogin"] is False
        assert roles[owner]["rolsuper"] is False
        assert roles[migrator]["rolcanlogin"] is True
        assert roles[migrator]["rolsuper"] is False
        assert roles[migrator]["rolbypassrls"] is False
        assert roles[runtime]["rolcanlogin"] is True
        assert roles[runtime]["rolsuper"] is False
        assert roles[runtime]["rolbypassrls"] is False
        with migrator_engine.connect() as conn:
            assert conn.execute(text("SELECT current_user")).scalar_one() == migrator
            assert conn.execute(text("SELECT session_user")).scalar_one() == migrator
        with runtime_engine.connect() as conn:
            assert conn.execute(text("SELECT current_user")).scalar_one() == runtime
            assert conn.execute(text("SELECT session_user")).scalar_one() == runtime

    def test_runtime_lacks_purge_and_version_mutation_privileges(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        with bootstrap_engine.begin() as conn:
            _, content_id, version_id = _seed_pair(conn, tenant_id=tenant_id)
        with runtime_engine.connect() as conn:
            with conn.begin():
                set_tenant(conn, tenant_id)
                _expect_dbapi(
                    conn,
                    lambda: conn.execute(
                        text("DELETE FROM content.contents WHERE content_id = :cid"),
                        {"cid": content_id},
                    ),
                    match="permission denied",
                )
                _expect_dbapi(
                    conn,
                    lambda: conn.execute(
                        text(
                            "UPDATE content.content_versions SET origin = 'SYSTEM' "
                            "WHERE version_id = :vid"
                        ),
                        {"vid": version_id},
                    ),
                    match="permission denied",
                )
                _expect_dbapi(
                    conn,
                    lambda: conn.execute(
                        text(
                            "DELETE FROM content.content_versions WHERE version_id = :vid"
                        ),
                        {"vid": version_id},
                    ),
                    match="permission denied",
                )


OFFLINE_DB = "aieos_gci_i02r2_offline"


def _generate_offline_upgrade_sql(migrator_url: str) -> str:
    cfg = alembic_config(migrator_url)
    output = io.StringIO()
    with redirect_stdout(output):
        command.upgrade(cfg, "base:head", sql=True)
    return output.getvalue()


def _exec_sql_script(url: str, script: str) -> None:
    engine = create_engine(url, isolation_level="AUTOCOMMIT")
    raw = engine.raw_connection()
    try:
        result = raw.pgconn.exec_(script.encode("utf-8"))
        if result is None or result.status == ExecStatus.FATAL_ERROR:
            message = result.error_message if result is not None else raw.pgconn.error_message
            raise RuntimeError(message)
    finally:
        raw.close()
        engine.dispose()


def _url_for_database(url: str, database: str) -> str:
    return make_url(url).set(database=database).render_as_string(hide_password=False)


class TestOfflineMigrationOwnership:
    def test_offline_sql_assumes_schema_owner_before_ddl_and_preserves_ownership(
        self, bootstrap_engine, postgres18
    ) -> None:
        sql_text = _generate_offline_upgrade_sql(postgres18["migrator_url"])
        role_stmt = f"SET LOCAL ROLE {SCHEMA_OWNER_ROLE}"
        create_schema = "CREATE SCHEMA content"
        create_api = "CREATE SCHEMA api"
        role_at = sql_text.find(role_stmt)
        create_at = sql_text.find(create_schema)
        api_at = sql_text.find(create_api)
        begin_at = sql_text.upper().find("BEGIN")
        assert role_at != -1, sql_text
        assert create_at != -1, sql_text
        assert api_at != -1, sql_text
        assert begin_at != -1, sql_text
        assert begin_at < role_at < create_at
        assert role_at < api_at

        autocommit = bootstrap_engine.execution_options(isolation_level="AUTOCOMMIT")
        with autocommit.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {OFFLINE_DB} WITH (FORCE)"))
            conn.execute(text(f"CREATE DATABASE {OFFLINE_DB}"))
            conn.execute(
                text(
                    f"GRANT CONNECT, CREATE ON DATABASE {OFFLINE_DB} "
                    f"TO {SCHEMA_OWNER_ROLE}"
                )
            )
            conn.execute(
                text(
                    f"GRANT CONNECT, CREATE ON DATABASE {OFFLINE_DB} "
                    f"TO {SECURITY_SCHEMA_OWNER_ROLE}"
                )
            )
            conn.execute(
                text(
                    f"GRANT CONNECT, CREATE ON DATABASE {OFFLINE_DB} "
                    f"TO {ASSET_SCHEMA_OWNER_ROLE}"
                )
            )
            conn.execute(
                text(
                    f"GRANT CONNECT ON DATABASE {OFFLINE_DB} TO {postgres18['migrator_user']}"
                )
            )

        bootstrap_offline_url = _url_for_database(postgres18["bootstrap_url"], OFFLINE_DB)
        bootstrap_offline = create_engine(bootstrap_offline_url, isolation_level="AUTOCOMMIT")
        try:
            provision_identities(bootstrap_offline)
            with bootstrap_offline.connect() as conn:
                conn.execute(
                    text(f"GRANT USAGE, CREATE ON SCHEMA public TO {SCHEMA_OWNER_ROLE}")
                )
                conn.execute(
                    text(
                        f"GRANT USAGE ON SCHEMA public TO {postgres18['migrator_user']}"
                    )
                )
            migrator_offline_url = _url_for_database(
                postgres18["migrator_url"], OFFLINE_DB
            )
            _exec_sql_script(migrator_offline_url, sql_text)
            with bootstrap_offline.connect() as conn:
                schema_owner = conn.execute(
                    text(
                        "SELECT pg_catalog.pg_get_userbyid(nspowner) "
                        "FROM pg_catalog.pg_namespace WHERE nspname = 'content'"
                    )
                ).scalar_one()
                table_owners = dict(
                    conn.execute(
                        text(
                            """
                            SELECT c.relname, pg_catalog.pg_get_userbyid(c.relowner)
                            FROM pg_catalog.pg_class c
                            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                            WHERE n.nspname = 'content' AND c.relkind = 'r'
                            """
                        )
                    ).all()
                )
                current_user = conn.execute(text("SELECT current_user")).scalar_one()
                api_owner = conn.execute(
                    text(
                        "SELECT pg_catalog.pg_get_userbyid(nspowner) "
                        "FROM pg_catalog.pg_namespace WHERE nspname = 'api'"
                    )
                ).scalar_one()
                api_table_owners = dict(
                    conn.execute(
                        text(
                            """
                            SELECT c.relname, pg_catalog.pg_get_userbyid(c.relowner)
                            FROM pg_catalog.pg_class c
                            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                            WHERE n.nspname = 'api' AND c.relkind = 'r'
                            """
                        )
                    ).all()
                )
            assert schema_owner == SCHEMA_OWNER_ROLE
            assert table_owners["contents"] == SCHEMA_OWNER_ROLE
            assert table_owners["content_versions"] == SCHEMA_OWNER_ROLE
            assert api_owner == SCHEMA_OWNER_ROLE
            assert api_table_owners["idempotency_records"] == SCHEMA_OWNER_ROLE
            assert current_user != SCHEMA_OWNER_ROLE
            with bootstrap_offline.connect() as conn:
                security_owner = conn.execute(
                    text(
                        "SELECT pg_catalog.pg_get_userbyid(nspowner) "
                        "FROM pg_catalog.pg_namespace WHERE nspname = 'security'"
                    )
                ).scalar_one()
                security_table_owners = dict(
                    conn.execute(
                        text(
                            """
                            SELECT c.relname, pg_catalog.pg_get_userbyid(c.relowner)
                            FROM pg_catalog.pg_class c
                            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                            WHERE n.nspname = 'security' AND c.relkind = 'r'
                            """
                        )
                    ).all()
                )
            assert security_owner == SECURITY_SCHEMA_OWNER_ROLE
            assert security_table_owners["audit_records"] == SECURITY_SCHEMA_OWNER_ROLE
            assert security_owner != SCHEMA_OWNER_ROLE
            assert f"SET LOCAL ROLE {SECURITY_SCHEMA_OWNER_ROLE}" in sql_text
            assert sql_text.index(f"SET LOCAL ROLE {SECURITY_SCHEMA_OWNER_ROLE}") < sql_text.index(
                "CREATE SCHEMA security"
            )
            assert sql_text.rindex(role_stmt) > sql_text.index("CREATE SCHEMA security")
            with bootstrap_offline.connect() as conn:
                asset_owner = conn.execute(
                    text(
                        "SELECT pg_catalog.pg_get_userbyid(nspowner) "
                        "FROM pg_catalog.pg_namespace WHERE nspname = 'asset'"
                    )
                ).scalar_one()
                asset_table_owners = dict(
                    conn.execute(
                        text(
                            """
                            SELECT c.relname, pg_catalog.pg_get_userbyid(c.relowner)
                            FROM pg_catalog.pg_class c
                            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                            WHERE n.nspname = 'asset' AND c.relkind = 'r'
                            """
                        )
                    ).all()
                )
            assert asset_owner == ASSET_SCHEMA_OWNER_ROLE
            assert asset_table_owners["assets"] == ASSET_SCHEMA_OWNER_ROLE
            assert asset_owner != SCHEMA_OWNER_ROLE
            assert asset_owner != SECURITY_SCHEMA_OWNER_ROLE
            assert f"SET LOCAL ROLE {ASSET_SCHEMA_OWNER_ROLE}" in sql_text
            assert sql_text.index(f"SET LOCAL ROLE {ASSET_SCHEMA_OWNER_ROLE}") < sql_text.index(
                "CREATE SCHEMA asset"
            )
            assert sql_text.rindex(role_stmt) > sql_text.index("CREATE SCHEMA asset")
        finally:
            bootstrap_offline.dispose()
            with autocommit.connect() as conn:
                conn.execute(text(f"DROP DATABASE IF EXISTS {OFFLINE_DB} WITH (FORCE)"))


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
            for needle in (
                "audit_events",
                "consumer_inbox",
            ):
                if needle in text_src:
                    hits.append(f"{path.name}:{needle}")
        assert hits == []
        # GCI-I11 AI provenance check is authorized; later slices remain forbidden.
        assert (
            REPO_ROOT / "migrations" / "versions" / "gcii100001_version_asset_refs.py"
        ).is_file()
        assert (
            REPO_ROOT / "migrations" / "versions" / "gcii110001_ai_provenance.py"
        ).is_file()
        assert (
            REPO_ROOT / "migrations" / "versions" / "gcii130001_migration_import.py"
        ).is_file()
        assert (
            REPO_ROOT / "migrations" / "versions" / "gcii090001_publications.py"
        ).is_file()
        # GCI-I08 outbox is authorized; keep it outside Content domain packages.
        content_infra = (
            SRC_ROOT / "aieos" / "domains" / "content" / "infrastructure"
        )
        for path in content_infra.rglob("*.py"):
            if "outbox_messages" in path.read_text(encoding="utf-8"):
                hits.append(str(path.relative_to(SRC_ROOT)))
        assert hits == []

"""PED-I10B2 Asset PostgreSQL SoR, RLS, and immutability tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from tests.conftest import (
    ASSET_SCHEMA_OWNER_ROLE,
    SCHEMA_OWNER_ROLE,
    SECURITY_SCHEMA_OWNER_ROLE,
    alembic_config,
    provision_runtime_grants,
)
from tests.dbutil import REPO_ROOT, clear_asset_audit_rows_for_schema_downgrade, set_tenant

pytestmark = pytest.mark.ped_i10b2

SHA = "a" * 64
OPAQUE_PADDED_KEY = "  opaque/key/1  "
ASSET_TABLES = (
    "assets",
    "asset_revisions",
    "asset_revision_states",
    "deletion_evidence",
)
FORBIDDEN_TABLES = (
    "roles",
    "permissions",
    "acl",
    "acls",
    "shares",
    "asset_shares",
    "role_capabilities",
    "delegations",
    "break_glass_grants",
)
RESOURCE_TYPES = (
    "asset.image",
    "asset.document",
    "asset.audio",
    "asset.video",
)
FORBIDDEN_RESOURCE_TYPES = (
    "asset.*",
    "*",
    "asset.file",
    "asset.binary",
    "asset.other",
    "asset.pdf",
    "ASSET.IMAGE",
    "Asset.Image",
    "asset.Image",
)
CONTENT_TABLES = {
    "contents",
    "content_versions",
    "review_decisions",
    "publications",
    "version_asset_refs",
    "migration_import_records",
}
SECURITY_TABLES = {
    "audit_records",
    "principals",
    "tenants",
    "tenant_memberships",
    "capability_grants",
}


def _now() -> datetime:
    return datetime(2026, 8, 17, 12, 0, tzinfo=UTC)


def _expect_integrity(conn, thunk) -> None:
    with pytest.raises(IntegrityError):
        with conn.begin_nested():
            thunk()


def _expect_dbapi(conn, thunk, match: str | None = None) -> None:
    with pytest.raises(DBAPIError, match=match):
        with conn.begin_nested():
            thunk()


def _insert_asset(
    conn,
    *,
    tenant_id: uuid.UUID,
    asset_id: uuid.UUID | None = None,
    resource_type: str = "asset.image",
    lifecycle: str = "active",
    quarantine_state: str = "clear",
    current_revision: int | None = None,
    aggregate_revision: int = 0,
    created_by: uuid.UUID | None = None,
) -> uuid.UUID:
    asset_id = asset_id or uuid.uuid7()
    conn.execute(
        text(
            """
            INSERT INTO asset.assets (
                tenant_id, asset_id, resource_type, lifecycle, quarantine_state,
                current_revision, aggregate_revision, created_at,
                created_by_principal_id
            ) VALUES (
                :tenant_id, :asset_id, :resource_type, :lifecycle,
                :quarantine_state, :current_revision, :aggregate_revision,
                :created_at, :created_by
            )
            """
        ),
        {
            "tenant_id": tenant_id,
            "asset_id": asset_id,
            "resource_type": resource_type,
            "lifecycle": lifecycle,
            "quarantine_state": quarantine_state,
            "current_revision": current_revision,
            "aggregate_revision": aggregate_revision,
            "created_at": _now(),
            "created_by": created_by or uuid.uuid7(),
        },
    )
    return asset_id


def _insert_revision(
    conn,
    *,
    tenant_id: uuid.UUID,
    asset_id: uuid.UUID,
    asset_revision_id: uuid.UUID | None = None,
    revision_number: int = 1,
    resource_type: str = "asset.image",
    storage_key: str = "opaque/key/1",
    media_type: str = "image/png",
    byte_size: int = 0,
    sha256: str = SHA,
) -> uuid.UUID:
    asset_revision_id = asset_revision_id or uuid.uuid7()
    conn.execute(
        text(
            """
            INSERT INTO asset.asset_revisions (
                asset_revision_id, tenant_id, asset_id, revision_number,
                resource_type, storage_key, media_type, byte_size, sha256,
                created_at, created_by_principal_id
            ) VALUES (
                :asset_revision_id, :tenant_id, :asset_id, :revision_number,
                :resource_type, :storage_key, :media_type, :byte_size, :sha256,
                :created_at, :created_by
            )
            """
        ),
        {
            "asset_revision_id": asset_revision_id,
            "tenant_id": tenant_id,
            "asset_id": asset_id,
            "revision_number": revision_number,
            "resource_type": resource_type,
            "storage_key": storage_key,
            "media_type": media_type,
            "byte_size": byte_size,
            "sha256": sha256,
            "created_at": _now(),
            "created_by": uuid.uuid7(),
        },
    )
    return asset_revision_id


def _insert_state(
    conn,
    *,
    tenant_id: uuid.UUID,
    asset_id: uuid.UUID,
    asset_revision_id: uuid.UUID,
    revision_number: int = 1,
    safety_state: str = "pending",
    bytes_purged: bool = False,
) -> None:
    conn.execute(
        text(
            """
            INSERT INTO asset.asset_revision_states (
                asset_revision_id, tenant_id, asset_id, revision_number,
                safety_state, bytes_purged, updated_at
            ) VALUES (
                :asset_revision_id, :tenant_id, :asset_id, :revision_number,
                :safety_state, :bytes_purged, :updated_at
            )
            """
        ),
        {
            "asset_revision_id": asset_revision_id,
            "tenant_id": tenant_id,
            "asset_id": asset_id,
            "revision_number": revision_number,
            "safety_state": safety_state,
            "bytes_purged": bytes_purged,
            "updated_at": _now(),
        },
    )


def _insert_evidence(
    conn,
    *,
    tenant_id: uuid.UUID,
    asset_id: uuid.UUID,
    asset_revision_id: uuid.UUID,
    revision_number: int = 1,
) -> None:
    conn.execute(
        text(
            """
            INSERT INTO asset.deletion_evidence (
                asset_revision_id, tenant_id, asset_id, revision_number,
                purged_at, purged_by_principal_id
            ) VALUES (
                :asset_revision_id, :tenant_id, :asset_id, :revision_number,
                :purged_at, :purged_by
            )
            """
        ),
        {
            "asset_revision_id": asset_revision_id,
            "tenant_id": tenant_id,
            "asset_id": asset_id,
            "revision_number": revision_number,
            "purged_at": _now(),
            "purged_by": uuid.uuid7(),
        },
    )


def _seed_revision(
    conn,
    *,
    tenant_id: uuid.UUID | None = None,
    resource_type: str = "asset.image",
    lifecycle: str = "active",
    current_revision: int | None = None,
    aggregate_revision: int = 0,
    storage_key: str = "opaque/key/1",
    revision_number: int = 1,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    tenant_id = tenant_id or uuid.uuid7()
    asset_id = _insert_asset(
        conn,
        tenant_id=tenant_id,
        resource_type=resource_type,
        lifecycle=lifecycle,
        current_revision=current_revision,
        aggregate_revision=aggregate_revision,
    )
    revision_id = _insert_revision(
        conn,
        tenant_id=tenant_id,
        asset_id=asset_id,
        revision_number=revision_number,
        resource_type=resource_type,
        storage_key=storage_key,
    )
    return tenant_id, asset_id, revision_id


def _table_columns(conn, table: str) -> set[str]:
    return {
        row[0]
        for row in conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'asset' AND table_name = :table"
            ),
            {"table": table},
        )
    }


def _fk_targets(conn) -> set[tuple[str, str]]:
    return {
        (row[0], row[1])
        for row in conn.execute(
            text(
                """
                SELECT DISTINCT nsp.nspname, rel.relname
                FROM pg_constraint con
                JOIN pg_class src ON src.oid = con.conrelid
                JOIN pg_namespace src_n ON src_n.oid = src.relnamespace
                JOIN pg_class rel ON rel.oid = con.confrelid
                JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace
                WHERE con.contype = 'f' AND src_n.nspname = 'asset'
                """
            )
        )
    }


class TestMigrationGraph:
    def test_script_directory_single_head_and_parent(self) -> None:
        cfg = Config(str(REPO_ROOT / "alembic.ini"))
        script = ScriptDirectory.from_config(cfg)
        assert script.get_heads() == ["tosd030001"]
        revision = script.get_revision("pedi10b2001")
        assert revision is not None
        assert revision.down_revision == "pedi090001"
        parent = script.get_revision("pedi090001")
        assert parent is not None
        assert parent.revision == "pedi090001"

    def test_database_head_is_pedi10b2001(self, bootstrap_engine) -> None:
        with bootstrap_engine.connect() as conn:
            assert (
                conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "tosd030001"
            )

    def test_downgrade_removes_asset_keeps_content_security_then_reupgrade(
        self, postgres18, bootstrap_engine
    ) -> None:
        cfg = alembic_config(postgres18["migrator_url"])
        clear_asset_audit_rows_for_schema_downgrade(bootstrap_engine)
        command.downgrade(cfg, "pedi090001")
        insp = inspect(bootstrap_engine)
        schemas = set(insp.get_schema_names())
        assert "asset" not in schemas
        assert "content" in schemas
        assert "security" in schemas
        assert set(insp.get_table_names(schema="content")) == CONTENT_TABLES
        assert set(insp.get_table_names(schema="security")) == SECURITY_TABLES
        with bootstrap_engine.connect() as conn:
            assert (
                conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "pedi090001"
            )
        command.upgrade(cfg, "head")
        provision_runtime_grants(bootstrap_engine)
        insp = inspect(bootstrap_engine)
        assert "asset" in insp.get_schema_names()
        assert set(insp.get_table_names(schema="asset")) == set(ASSET_TABLES)
        with bootstrap_engine.connect() as conn:
            assert (
                conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "tosd030001"
            )


class TestExactSchema:
    def test_four_tables_and_no_acl_authority(self, bootstrap_engine) -> None:
        insp = inspect(bootstrap_engine)
        assert "asset" in insp.get_schema_names()
        tables = set(insp.get_table_names(schema="asset"))
        assert tables == set(ASSET_TABLES)
        for forbidden in FORBIDDEN_TABLES:
            assert forbidden not in tables
        with bootstrap_engine.connect() as conn:
            assets_cols = _table_columns(conn, "assets")
            revision_cols = _table_columns(conn, "asset_revisions")
            state_cols = _table_columns(conn, "asset_revision_states")
            evidence_cols = _table_columns(conn, "deletion_evidence")
        assert assets_cols == {
            "tenant_id",
            "asset_id",
            "resource_type",
            "lifecycle",
            "quarantine_state",
            "current_revision",
            "aggregate_revision",
            "created_at",
            "created_by_principal_id",
        }
        assert revision_cols == {
            "asset_revision_id",
            "tenant_id",
            "asset_id",
            "revision_number",
            "resource_type",
            "storage_key",
            "media_type",
            "byte_size",
            "sha256",
            "created_at",
            "created_by_principal_id",
        }
        assert state_cols == {
            "asset_revision_id",
            "tenant_id",
            "asset_id",
            "revision_number",
            "safety_state",
            "bytes_purged",
            "updated_at",
        }
        assert evidence_cols == {
            "asset_revision_id",
            "tenant_id",
            "asset_id",
            "revision_number",
            "purged_at",
            "purged_by_principal_id",
        }
        for forbidden_col in (
            "owner_principal_id",
            "blob_exists",
            "usable",
            "available",
            "storage_url",
            "bucket",
            "content_id",
            "safety_state",
        ):
            assert forbidden_col not in assets_cols
        for forbidden_col in (
            "safety_state",
            "quarantine_state",
            "withdrawn",
            "deleted",
            "available",
            "usable",
            "blob_exists",
            "bytes_purged",
        ):
            assert forbidden_col not in revision_cols
        for forbidden_col in ("blob_exists", "available", "usable"):
            assert forbidden_col not in state_cols
            assert forbidden_col not in evidence_cols

    def test_required_indexes_exist(self, bootstrap_engine) -> None:
        with bootstrap_engine.connect() as conn:
            names = set(
                conn.execute(
                    text(
                        """
                        SELECT indexname FROM pg_indexes
                        WHERE schemaname = 'asset'
                        """
                    )
                ).scalars()
            )
        assert "ix_assets_tenant_id" in names
        assert "ix_assets_tenant_resource_type" in names
        assert "ix_assets_tenant_lifecycle" in names
        assert "uq_asset_revisions_tenant_asset_number" in names
        assert "ix_asset_revision_states_tenant_asset_number" in names
        assert "ix_deletion_evidence_tenant_asset_number" in names


class TestVocabulariesAndNumbers:
    @pytest.mark.parametrize("resource_type", RESOURCE_TYPES)
    def test_exact_resource_types_insert(self, bootstrap_engine, resource_type) -> None:
        with bootstrap_engine.begin() as conn:
            _insert_asset(
                conn, tenant_id=uuid.uuid7(), resource_type=resource_type
            )

    @pytest.mark.parametrize("resource_type", FORBIDDEN_RESOURCE_TYPES)
    def test_invalid_resource_types_reject(
        self, bootstrap_engine, resource_type
    ) -> None:
        with bootstrap_engine.begin() as conn:
            _expect_integrity(
                conn,
                lambda: _insert_asset(
                    conn, tenant_id=uuid.uuid7(), resource_type=resource_type
                ),
            )

    @pytest.mark.parametrize("lifecycle", ("active", "withdrawn", "deleted"))
    def test_exact_lifecycles_insert(self, bootstrap_engine, lifecycle) -> None:
        with bootstrap_engine.begin() as conn:
            _insert_asset(conn, tenant_id=uuid.uuid7(), lifecycle=lifecycle)

    def test_bogus_lifecycle_rejects(self, bootstrap_engine) -> None:
        with bootstrap_engine.begin() as conn:
            _expect_integrity(
                conn,
                lambda: _insert_asset(
                    conn, tenant_id=uuid.uuid7(), lifecycle="archived"
                ),
            )

    @pytest.mark.parametrize("quarantine_state", ("clear", "quarantined"))
    def test_exact_quarantine_insert(self, bootstrap_engine, quarantine_state) -> None:
        with bootstrap_engine.begin() as conn:
            _insert_asset(
                conn, tenant_id=uuid.uuid7(), quarantine_state=quarantine_state
            )

    def test_bogus_quarantine_rejects(self, bootstrap_engine) -> None:
        with bootstrap_engine.begin() as conn:
            _expect_integrity(
                conn,
                lambda: _insert_asset(
                    conn, tenant_id=uuid.uuid7(), quarantine_state="safe"
                ),
            )

    @pytest.mark.parametrize("safety_state", ("pending", "passed", "failed"))
    def test_exact_safety_insert(self, bootstrap_engine, safety_state) -> None:
        with bootstrap_engine.begin() as conn:
            tenant_id, asset_id, revision_id = _seed_revision(conn)
            _insert_state(
                conn,
                tenant_id=tenant_id,
                asset_id=asset_id,
                asset_revision_id=revision_id,
                safety_state=safety_state,
            )

    def test_bogus_safety_rejects(self, bootstrap_engine) -> None:
        with bootstrap_engine.begin() as conn:
            tenant_id, asset_id, revision_id = _seed_revision(conn)
            _expect_integrity(
                conn,
                lambda: _insert_state(
                    conn,
                    tenant_id=tenant_id,
                    asset_id=asset_id,
                    asset_revision_id=revision_id,
                    safety_state="usable",
                ),
            )

    def test_revision_and_aggregate_number_rules(self, bootstrap_engine) -> None:
        with bootstrap_engine.begin() as conn:
            tenant_id, asset_id, _ = _seed_revision(conn, revision_number=1)
            _expect_integrity(
                conn,
                lambda: _insert_revision(
                    conn,
                    tenant_id=tenant_id,
                    asset_id=asset_id,
                    revision_number=0,
                ),
            )
            _expect_integrity(
                conn,
                lambda: _insert_revision(
                    conn,
                    tenant_id=tenant_id,
                    asset_id=asset_id,
                    revision_number=-1,
                ),
            )
            _expect_integrity(
                conn,
                lambda: _insert_revision(
                    conn,
                    tenant_id=tenant_id,
                    asset_id=asset_id,
                    revision_number=1,
                ),
            )
        with bootstrap_engine.begin() as conn:
            tenant_a = uuid.uuid7()
            tenant_b = uuid.uuid7()
            asset_a = _insert_asset(conn, tenant_id=tenant_a)
            asset_b = _insert_asset(conn, tenant_id=tenant_b)
            _insert_revision(conn, tenant_id=tenant_a, asset_id=asset_a, revision_number=1)
            _insert_revision(conn, tenant_id=tenant_b, asset_id=asset_b, revision_number=1)
        with bootstrap_engine.begin() as conn:
            _insert_asset(conn, tenant_id=uuid.uuid7(), aggregate_revision=0)
            _expect_integrity(
                conn,
                lambda: _insert_asset(
                    conn, tenant_id=uuid.uuid7(), aggregate_revision=-1
                ),
            )
            _insert_asset(
                conn, tenant_id=uuid.uuid7(), current_revision=None
            )
            _expect_integrity(
                conn,
                lambda: _insert_asset(
                    conn, tenant_id=uuid.uuid7(), current_revision=0
                ),
            )


class TestCurrentRevisionSemantics:
    def test_active_null_and_withdrawn_deleted_retain_revision(
        self, bootstrap_engine
    ) -> None:
        with bootstrap_engine.begin() as conn:
            tenant_id = uuid.uuid7()
            asset_id = _insert_asset(
                conn,
                tenant_id=tenant_id,
                lifecycle="active",
                current_revision=None,
            )
            revision_id = _insert_revision(
                conn, tenant_id=tenant_id, asset_id=asset_id, revision_number=1
            )
            conn.execute(
                text(
                    """
                    UPDATE asset.assets
                    SET lifecycle = 'withdrawn', current_revision = 1
                    WHERE asset_id = :asset_id
                    """
                ),
                {"asset_id": asset_id},
            )
            conn.execute(
                text(
                    """
                    UPDATE asset.assets
                    SET lifecycle = 'deleted', current_revision = 1
                    WHERE asset_id = :asset_id
                    """
                ),
                {"asset_id": asset_id},
            )
            row = conn.execute(
                text(
                    """
                    SELECT lifecycle, current_revision FROM asset.assets
                    WHERE asset_id = :asset_id
                    """
                ),
                {"asset_id": asset_id},
            ).one()
            assert row == ("deleted", 1)
            assert revision_id is not None

    def test_missing_current_revision_target_rejects_on_commit(
        self, bootstrap_engine
    ) -> None:
        with pytest.raises((IntegrityError, DBAPIError)):
            with bootstrap_engine.begin() as conn:
                _insert_asset(
                    conn, tenant_id=uuid.uuid7(), current_revision=1
                )

    def test_revision_insert_does_not_auto_activate_or_bump_aggregate(
        self, bootstrap_engine
    ) -> None:
        with bootstrap_engine.begin() as conn:
            tenant_id, asset_id, _ = _seed_revision(
                conn, current_revision=None, aggregate_revision=0
            )
            row = conn.execute(
                text(
                    """
                    SELECT current_revision, aggregate_revision
                    FROM asset.assets WHERE asset_id = :asset_id
                    """
                ),
                {"asset_id": asset_id},
            ).one()
            assert row == (None, 0)
            _insert_revision(
                conn,
                tenant_id=tenant_id,
                asset_id=asset_id,
                revision_number=2,
            )
            row = conn.execute(
                text(
                    """
                    SELECT current_revision, aggregate_revision
                    FROM asset.assets WHERE asset_id = :asset_id
                    """
                ),
                {"asset_id": asset_id},
            ).one()
            assert row == (None, 0)

    def test_no_auto_activate_trigger_or_silent_fallback(
        self, bootstrap_engine
    ) -> None:
        with bootstrap_engine.connect() as conn:
            triggers = list(
                conn.execute(
                    text(
                        """
                        SELECT c.relname, t.tgname, pg_get_triggerdef(t.oid)
                        FROM pg_trigger t
                        JOIN pg_class c ON c.oid = t.tgrelid
                        JOIN pg_namespace n ON n.oid = c.relnamespace
                        WHERE n.nspname = 'asset' AND NOT t.tgisinternal
                        """
                    )
                )
            )
            functions = list(
                conn.execute(
                    text(
                        """
                        SELECT p.proname, pg_get_functiondef(p.oid)
                        FROM pg_proc p
                        JOIN pg_namespace n ON n.oid = p.pronamespace
                        WHERE n.nspname = 'asset'
                        """
                    )
                )
            )
        names = {(row[0], row[1]) for row in triggers}
        assert names == {
            ("asset_revisions", "asset_revisions_immutable_update"),
            ("asset_revisions", "asset_revisions_immutable_delete"),
            ("deletion_evidence", "deletion_evidence_immutable_update"),
            ("deletion_evidence", "deletion_evidence_immutable_delete"),
        }
        for _table, _name, definition in triggers:
            assert "current_revision" not in definition
            assert "aggregate_revision" not in definition
        for name, definition in functions:
            assert name in {"current_tenant_id", "reject_immutable_row_mutation"}
            assert "NEW.current_revision" not in definition
            assert "UPDATE asset.assets" not in definition

    def test_current_revision_cannot_target_other_tenant_or_asset(
        self, bootstrap_engine
    ) -> None:
        with pytest.raises((IntegrityError, DBAPIError)):
            with bootstrap_engine.begin() as conn:
                tenant_a, asset_a, _ = _seed_revision(conn)
                tenant_b = uuid.uuid7()
                _insert_asset(
                    conn,
                    tenant_id=tenant_b,
                    current_revision=1,
                )
                assert tenant_a != tenant_b
                assert asset_a is not None
        with pytest.raises((IntegrityError, DBAPIError)):
            with bootstrap_engine.begin() as conn:
                tenant_id = uuid.uuid7()
                asset_a = _insert_asset(conn, tenant_id=tenant_id)
                _insert_revision(
                    conn, tenant_id=tenant_id, asset_id=asset_a, revision_number=1
                )
                _insert_asset(
                    conn,
                    tenant_id=tenant_id,
                    current_revision=1,
                )


class TestRevisionImmutabilityAndFacts:
    def test_update_and_delete_rejected(self, bootstrap_engine) -> None:
        with bootstrap_engine.begin() as conn:
            _tenant_id, _asset_id, revision_id = _seed_revision(conn)
            _expect_dbapi(
                conn,
                lambda: conn.execute(
                    text(
                        "UPDATE asset.asset_revisions SET byte_size = 9 "
                        "WHERE asset_revision_id = :id"
                    ),
                    {"id": revision_id},
                ),
                match="immutable",
            )
        with bootstrap_engine.begin() as conn:
            _tenant_id, _asset_id, revision_id = _seed_revision(conn)
            _expect_dbapi(
                conn,
                lambda: conn.execute(
                    text(
                        "DELETE FROM asset.asset_revisions "
                        "WHERE asset_revision_id = :id"
                    ),
                    {"id": revision_id},
                ),
                match="immutable",
            )

    def test_replacement_bytes_require_new_row(self, bootstrap_engine) -> None:
        with bootstrap_engine.begin() as conn:
            tenant_id, asset_id, first = _seed_revision(
                conn, storage_key="opaque/key/1"
            )
            second = _insert_revision(
                conn,
                tenant_id=tenant_id,
                asset_id=asset_id,
                revision_number=2,
                storage_key="opaque/key/2",
            )
            keys = set(
                conn.execute(
                    text(
                        "SELECT storage_key FROM asset.asset_revisions "
                        "WHERE asset_id = :asset_id"
                    ),
                    {"asset_id": asset_id},
                ).scalars()
            )
            assert keys == {"opaque/key/1", "opaque/key/2"}
            assert first != second

    def test_storage_key_empty_whitespace_and_exact_opacity(
        self, bootstrap_engine
    ) -> None:
        with bootstrap_engine.begin() as conn:
            tenant_id, asset_id, _ = _seed_revision(conn)
            _expect_integrity(
                conn,
                lambda: _insert_revision(
                    conn,
                    tenant_id=tenant_id,
                    asset_id=asset_id,
                    revision_number=2,
                    storage_key="",
                ),
            )
            _expect_integrity(
                conn,
                lambda: _insert_revision(
                    conn,
                    tenant_id=tenant_id,
                    asset_id=asset_id,
                    revision_number=3,
                    storage_key="   ",
                ),
            )
        with bootstrap_engine.begin() as conn:
            tenant_id, asset_id, revision_id = _seed_revision(
                conn, storage_key=OPAQUE_PADDED_KEY
            )
            stored = conn.execute(
                text(
                    "SELECT storage_key FROM asset.asset_revisions "
                    "WHERE asset_revision_id = :id"
                ),
                {"id": revision_id},
            ).scalar_one()
            assert stored == OPAQUE_PADDED_KEY
            assert stored != stored.strip()

    def test_byte_facts_and_media_type(self, bootstrap_engine) -> None:
        with bootstrap_engine.begin() as conn:
            tenant_id, asset_id, _ = _seed_revision(conn)
            _expect_integrity(
                conn,
                lambda: _insert_revision(
                    conn,
                    tenant_id=tenant_id,
                    asset_id=asset_id,
                    revision_number=2,
                    byte_size=-1,
                ),
            )
            _insert_revision(
                conn,
                tenant_id=tenant_id,
                asset_id=asset_id,
                revision_number=3,
                sha256="ab" * 32,
            )
            _expect_integrity(
                conn,
                lambda: _insert_revision(
                    conn,
                    tenant_id=tenant_id,
                    asset_id=asset_id,
                    revision_number=4,
                    sha256="A" * 64,
                ),
            )
            _expect_integrity(
                conn,
                lambda: _insert_revision(
                    conn,
                    tenant_id=tenant_id,
                    asset_id=asset_id,
                    revision_number=5,
                    sha256="zzzz",
                ),
            )
            _expect_integrity(
                conn,
                lambda: _insert_revision(
                    conn,
                    tenant_id=tenant_id,
                    asset_id=asset_id,
                    revision_number=6,
                    media_type="",
                ),
            )
            _expect_integrity(
                conn,
                lambda: _insert_revision(
                    conn,
                    tenant_id=tenant_id,
                    asset_id=asset_id,
                    revision_number=7,
                    media_type="   ",
                ),
            )

    def test_resource_type_must_match_owning_asset(self, bootstrap_engine) -> None:
        with bootstrap_engine.begin() as conn:
            tenant_id = uuid.uuid7()
            asset_id = _insert_asset(
                conn, tenant_id=tenant_id, resource_type="asset.image"
            )
            _insert_revision(
                conn,
                tenant_id=tenant_id,
                asset_id=asset_id,
                resource_type="asset.image",
            )
            _expect_integrity(
                conn,
                lambda: _insert_revision(
                    conn,
                    tenant_id=tenant_id,
                    asset_id=asset_id,
                    revision_number=2,
                    resource_type="asset.document",
                ),
            )


class TestRevisionStateAndEvidence:
    def test_state_fk_and_mismatch_and_bytes_purged(self, bootstrap_engine) -> None:
        with bootstrap_engine.begin() as conn:
            tenant_id, asset_id, revision_id = _seed_revision(conn)
            _insert_state(
                conn,
                tenant_id=tenant_id,
                asset_id=asset_id,
                asset_revision_id=revision_id,
                bytes_purged=False,
            )
            conn.execute(
                text(
                    "UPDATE asset.asset_revision_states SET bytes_purged = TRUE, "
                    "safety_state = 'passed' WHERE asset_revision_id = :id"
                ),
                {"id": revision_id},
            )
            _expect_integrity(
                conn,
                lambda: _insert_state(
                    conn,
                    tenant_id=tenant_id,
                    asset_id=asset_id,
                    asset_revision_id=uuid.uuid7(),
                ),
            )
            other_asset = _insert_asset(conn, tenant_id=tenant_id)
            other_revision = _insert_revision(
                conn, tenant_id=tenant_id, asset_id=other_asset
            )
            _expect_integrity(
                conn,
                lambda: _insert_state(
                    conn,
                    tenant_id=tenant_id,
                    asset_id=asset_id,
                    asset_revision_id=other_revision,
                ),
            )

    def test_deletion_evidence_identity_immutability_and_no_lifecycle_side_effect(
        self, bootstrap_engine
    ) -> None:
        with bootstrap_engine.begin() as conn:
            tenant_id, asset_id, revision_id = _seed_revision(
                conn, lifecycle="active"
            )
            _insert_evidence(
                conn,
                tenant_id=tenant_id,
                asset_id=asset_id,
                asset_revision_id=revision_id,
            )
            lifecycle = conn.execute(
                text(
                    "SELECT lifecycle FROM asset.assets WHERE asset_id = :id"
                ),
                {"id": asset_id},
            ).scalar_one()
            assert lifecycle == "active"
            _expect_integrity(
                conn,
                lambda: _insert_evidence(
                    conn,
                    tenant_id=tenant_id,
                    asset_id=asset_id,
                    asset_revision_id=revision_id,
                ),
            )
            _expect_integrity(
                conn,
                lambda: _insert_evidence(
                    conn,
                    tenant_id=tenant_id,
                    asset_id=asset_id,
                    asset_revision_id=uuid.uuid7(),
                ),
            )
            _expect_dbapi(
                conn,
                lambda: conn.execute(
                    text(
                        "UPDATE asset.deletion_evidence SET revision_number = 9 "
                        "WHERE asset_revision_id = :id"
                    ),
                    {"id": revision_id},
                ),
                match="immutable",
            )
            _expect_dbapi(
                conn,
                lambda: conn.execute(
                    text(
                        "DELETE FROM asset.deletion_evidence "
                        "WHERE asset_revision_id = :id"
                    ),
                    {"id": revision_id},
                ),
                match="immutable",
            )


class TestRlsAndPrivileges:
    def test_enable_force_and_explicit_policies(self, bootstrap_engine) -> None:
        with bootstrap_engine.connect() as conn:
            for table in ASSET_TABLES:
                row = conn.execute(
                    text(
                        """
                        SELECT c.relrowsecurity, c.relforcerowsecurity
                        FROM pg_class c
                        JOIN pg_namespace n ON n.oid = c.relnamespace
                        WHERE n.nspname = 'asset' AND c.relname = :table
                        """
                    ),
                    {"table": table},
                ).one()
                assert row == (True, True)
                policies = conn.execute(
                    text(
                        """
                        SELECT polname, polcmd, pg_get_expr(polqual, polrelid),
                               pg_get_expr(polwithcheck, polrelid)
                        FROM pg_policy p
                        JOIN pg_class c ON c.oid = p.polrelid
                        JOIN pg_namespace n ON n.oid = c.relnamespace
                        WHERE n.nspname = 'asset' AND c.relname = :table
                        """
                    ),
                    {"table": table},
                ).all()
                assert len(policies) == 1
                name, cmd, using, check = policies[0]
                assert cmd == "*"
                assert "asset.current_tenant_id()" in using
                assert "asset.current_tenant_id()" in check
                assert "tenant_id" in using
                assert name.endswith("_tenant_isolation")

    def test_missing_tenant_context_fails_closed(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id, asset_id, revision_id = (uuid.uuid7(), uuid.uuid7(), uuid.uuid7())
        with bootstrap_engine.begin() as conn:
            _insert_asset(conn, tenant_id=tenant_id, asset_id=asset_id)
            _insert_revision(
                conn, tenant_id=tenant_id, asset_id=asset_id,
                asset_revision_id=revision_id,
            )
            _insert_state(
                conn,
                tenant_id=tenant_id,
                asset_id=asset_id,
                asset_revision_id=revision_id,
            )
            _insert_evidence(
                conn,
                tenant_id=tenant_id,
                asset_id=asset_id,
                asset_revision_id=revision_id,
            )
        for table in (
            "asset.assets",
            "asset.asset_revisions",
            "asset.asset_revision_states",
            "asset.deletion_evidence",
        ):
            with runtime_engine.connect() as conn:
                with conn.begin():
                    with pytest.raises(DBAPIError, match="aieos.tenant_id"):
                        conn.execute(text(f"SELECT * FROM {table}")).fetchall()
        with runtime_engine.connect() as conn:
            with conn.begin():
                with pytest.raises(DBAPIError, match="aieos.tenant_id"):
                    _insert_asset(conn, tenant_id=tenant_id)
        with runtime_engine.connect() as conn:
            with conn.begin():
                with pytest.raises(DBAPIError, match="aieos.tenant_id"):
                    conn.execute(
                        text(
                            "UPDATE asset.assets SET lifecycle = 'withdrawn' "
                            "WHERE asset_id = :id"
                        ),
                        {"id": asset_id},
                    )

    def test_cross_tenant_isolation_and_write_checks(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_a = uuid.uuid7()
        tenant_b = uuid.uuid7()
        with bootstrap_engine.begin() as conn:
            asset_a = _insert_asset(conn, tenant_id=tenant_a)
            revision_a = _insert_revision(
                conn, tenant_id=tenant_a, asset_id=asset_a
            )
            _insert_state(
                conn,
                tenant_id=tenant_a,
                asset_id=asset_a,
                asset_revision_id=revision_a,
            )
            _insert_evidence(
                conn,
                tenant_id=tenant_a,
                asset_id=asset_a,
                asset_revision_id=revision_a,
            )
            asset_b = _insert_asset(conn, tenant_id=tenant_b)
            revision_b = _insert_revision(
                conn, tenant_id=tenant_b, asset_id=asset_b
            )
            _insert_state(
                conn,
                tenant_id=tenant_b,
                asset_id=asset_b,
                asset_revision_id=revision_b,
            )
            _insert_evidence(
                conn,
                tenant_id=tenant_b,
                asset_id=asset_b,
                asset_revision_id=revision_b,
            )
        with runtime_engine.connect() as conn:
            with conn.begin():
                set_tenant(conn, tenant_a)
                assert conn.execute(
                    text("SELECT asset_id FROM asset.assets")
                ).scalars().all() == [asset_a]
                assert conn.execute(
                    text("SELECT asset_revision_id FROM asset.asset_revisions")
                ).scalars().all() == [revision_a]
                assert conn.execute(
                    text(
                        "SELECT asset_revision_id FROM asset.asset_revision_states"
                    )
                ).scalars().all() == [revision_a]
                assert conn.execute(
                    text("SELECT asset_revision_id FROM asset.deletion_evidence")
                ).scalars().all() == [revision_a]
                with pytest.raises(DBAPIError):
                    _insert_asset(conn, tenant_id=tenant_b)
                with pytest.raises(DBAPIError):
                    conn.execute(
                        text(
                            "UPDATE asset.assets SET tenant_id = :other "
                            "WHERE asset_id = :id"
                        ),
                        {"other": tenant_b, "id": asset_a},
                    )

    def test_transaction_local_tenant_context_does_not_leak(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_a = uuid.uuid7()
        tenant_b = uuid.uuid7()
        with bootstrap_engine.begin() as conn:
            _insert_asset(conn, tenant_id=tenant_a)
            _insert_asset(conn, tenant_id=tenant_b)
        with runtime_engine.connect() as conn:
            with conn.begin():
                set_tenant(conn, tenant_a)
                assert (
                    conn.execute(text("SELECT count(*) FROM asset.assets")).scalar_one()
                    == 1
                )
            with conn.begin():
                with pytest.raises(DBAPIError, match="aieos.tenant_id"):
                    conn.execute(text("SELECT count(*) FROM asset.assets")).scalar_one()
            with conn.begin():
                set_tenant(conn, tenant_b)
                tenants = conn.execute(
                    text("SELECT tenant_id FROM asset.assets")
                ).scalars().all()
                assert tenants == [tenant_b]

    def test_public_has_no_asset_access(self, bootstrap_engine) -> None:
        with bootstrap_engine.connect() as conn:
            assert conn.execute(
                text("SELECT has_schema_privilege('public', 'asset', 'USAGE')")
            ).scalar_one() is False
            for table in ASSET_TABLES:
                for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                    assert conn.execute(
                        text(
                            "SELECT has_table_privilege("
                            "'public', :rel, :priv)"
                        ),
                        {"rel": f"asset.{table}", "priv": privilege},
                    ).scalar_one() is False
            assert conn.execute(
                text(
                    "SELECT has_function_privilege("
                    "'public', 'asset.current_tenant_id()', 'EXECUTE')"
                )
            ).scalar_one() is False
            assert conn.execute(
                text(
                    "SELECT has_function_privilege("
                    "'public', 'asset.reject_immutable_row_mutation()', 'EXECUTE')"
                )
            ).scalar_one() is False

    def test_owners_distinct_and_nobypassrls(
        self, bootstrap_engine, runtime_engine, postgres18
    ) -> None:
        with bootstrap_engine.connect() as conn:
            owners = dict(
                conn.execute(
                    text(
                        """
                        SELECT n.nspname, pg_get_userbyid(n.nspowner)
                        FROM pg_namespace n
                        WHERE n.nspname IN ('asset', 'content', 'security')
                        """
                    )
                ).all()
            )
            roles = {
                row["rolname"]: row
                for row in conn.execute(
                    text(
                        """
                        SELECT rolname, rolsuper, rolbypassrls, rolcanlogin
                        FROM pg_roles
                        WHERE rolname IN (:asset, :content, :security, :runtime)
                        """
                    ),
                    {
                        "asset": ASSET_SCHEMA_OWNER_ROLE,
                        "content": SCHEMA_OWNER_ROLE,
                        "security": SECURITY_SCHEMA_OWNER_ROLE,
                        "runtime": postgres18["runtime_user"],
                    },
                ).mappings()
            }
        assert owners["asset"] == ASSET_SCHEMA_OWNER_ROLE
        assert owners["content"] == SCHEMA_OWNER_ROLE
        assert owners["security"] == SECURITY_SCHEMA_OWNER_ROLE
        assert owners["asset"] != owners["content"]
        assert owners["asset"] != owners["security"]
        assert roles[ASSET_SCHEMA_OWNER_ROLE]["rolbypassrls"] is False
        assert roles[ASSET_SCHEMA_OWNER_ROLE]["rolcanlogin"] is False
        assert roles[ASSET_SCHEMA_OWNER_ROLE]["rolsuper"] is False
        assert roles[postgres18["runtime_user"]]["rolbypassrls"] is False
        with runtime_engine.connect() as conn:
            assert (
                conn.execute(text("SELECT current_user")).scalar_one()
                == postgres18["runtime_user"]
            )

    def test_runtime_cannot_mutate_immutable_or_delete_mutable(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id, asset_id, revision_id = (uuid.uuid7(), uuid.uuid7(), uuid.uuid7())
        with bootstrap_engine.begin() as conn:
            _insert_asset(conn, tenant_id=tenant_id, asset_id=asset_id)
            _insert_revision(
                conn,
                tenant_id=tenant_id,
                asset_id=asset_id,
                asset_revision_id=revision_id,
            )
            _insert_state(
                conn,
                tenant_id=tenant_id,
                asset_id=asset_id,
                asset_revision_id=revision_id,
            )
            _insert_evidence(
                conn,
                tenant_id=tenant_id,
                asset_id=asset_id,
                asset_revision_id=revision_id,
            )
        with runtime_engine.connect() as conn:
            with conn.begin():
                set_tenant(conn, tenant_id)
                _expect_dbapi(
                    conn,
                    lambda: conn.execute(
                        text(
                            "UPDATE asset.asset_revisions SET byte_size = 1 "
                            "WHERE asset_revision_id = :id"
                        ),
                        {"id": revision_id},
                    ),
                    match="permission denied",
                )
                _expect_dbapi(
                    conn,
                    lambda: conn.execute(
                        text(
                            "DELETE FROM asset.asset_revisions "
                            "WHERE asset_revision_id = :id"
                        ),
                        {"id": revision_id},
                    ),
                    match="permission denied",
                )
                _expect_dbapi(
                    conn,
                    lambda: conn.execute(
                        text(
                            "UPDATE asset.deletion_evidence SET revision_number = 9 "
                            "WHERE asset_revision_id = :id"
                        ),
                        {"id": revision_id},
                    ),
                    match="permission denied",
                )
                _expect_dbapi(
                    conn,
                    lambda: conn.execute(
                        text(
                            "DELETE FROM asset.deletion_evidence "
                            "WHERE asset_revision_id = :id"
                        ),
                        {"id": revision_id},
                    ),
                    match="permission denied",
                )
                _expect_dbapi(
                    conn,
                    lambda: conn.execute(
                        text("DELETE FROM asset.assets WHERE asset_id = :id"),
                        {"id": asset_id},
                    ),
                    match="permission denied",
                )
                _expect_dbapi(
                    conn,
                    lambda: conn.execute(
                        text(
                            "DELETE FROM asset.asset_revision_states "
                            "WHERE asset_revision_id = :id"
                        ),
                        {"id": revision_id},
                    ),
                    match="permission denied",
                )


class TestCrossDomainGuards:
    def test_no_cross_domain_fk_and_content_unchanged(self, bootstrap_engine) -> None:
        with bootstrap_engine.connect() as conn:
            targets = _fk_targets(conn)
            assert targets == {("asset", "assets"), ("asset", "asset_revisions")}
            content_tables = {
                row[0]
                for row in conn.execute(
                    text(
                        "SELECT tablename FROM pg_tables WHERE schemaname = 'content'"
                    )
                )
            }
            assert content_tables == CONTENT_TABLES
            version_asset_ref_fks = {
                row[0]
                for row in conn.execute(
                    text(
                        """
                        SELECT con.conname
                        FROM pg_constraint con
                        JOIN pg_class src ON src.oid = con.conrelid
                        JOIN pg_namespace n ON n.oid = src.relnamespace
                        WHERE n.nspname = 'content'
                          AND src.relname = 'version_asset_refs'
                          AND con.contype = 'f'
                        """
                    )
                )
            }
            assert version_asset_ref_fks == {"fk_version_asset_refs_version"}


class TestRoleContract:
    def test_missing_or_malformed_asset_owner_fails_closed(self, monkeypatch) -> None:
        import importlib.util

        path = (
            REPO_ROOT / "migrations" / "versions" / "pedi10b2001_asset_authority_sor.py"
        )
        spec = importlib.util.spec_from_file_location("pedi10b2001", path)
        assert spec is not None and spec.loader is not None
        mig = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mig)

        monkeypatch.delenv("AIEOS_ASSET_SCHEMA_OWNER_ROLE", raising=False)
        with pytest.raises(RuntimeError, match="AIEOS_ASSET_SCHEMA_OWNER_ROLE"):
            mig._require_role(
                "AIEOS_ASSET_SCHEMA_OWNER_ROLE", purpose="Asset schema-owner role"
            )
        monkeypatch.setenv("AIEOS_ASSET_SCHEMA_OWNER_ROLE", "Aieos Asset")
        with pytest.raises(RuntimeError, match="lowercase unquoted"):
            mig._require_role(
                "AIEOS_ASSET_SCHEMA_OWNER_ROLE", purpose="Asset schema-owner role"
            )
        monkeypatch.setenv("AIEOS_ASSET_SCHEMA_OWNER_ROLE", "aieos_asset_owner")
        assert (
            mig._require_role(
                "AIEOS_ASSET_SCHEMA_OWNER_ROLE", purpose="Asset schema-owner role"
            )
            == "aieos_asset_owner"
        )

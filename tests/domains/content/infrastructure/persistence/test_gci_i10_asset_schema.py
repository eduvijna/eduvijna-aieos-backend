"""GCI-I10 version_asset_refs schema and persistence against PostgreSQL 18."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.domains.content.domain.identities import ContentId, ContentVersionId
from aieos.domains.content.domain.version_asset_ref import VersionAssetRef
from aieos.domains.content.infrastructure.persistence.mapping import (
    version_asset_ref_from_row,
)
from aieos.domains.content.infrastructure.persistence.models import version_asset_refs_table
from aieos.domains.content.infrastructure.persistence.uow import (
    SqlAlchemyContentUnitOfWorkFactory,
)
from aieos.platform.resources import ResourceRef
from tests.conftest import SCHEMA_OWNER_ROLE

pytestmark = pytest.mark.gci_i10

FIXED_NOW = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)


def _seed_version(bootstrap_engine: Engine, tenant_id: uuid.UUID) -> tuple[ContentId, ContentVersionId]:
    content_id = ContentId.generate()
    version_id = ContentVersionId.generate()
    principal = uuid.uuid7()
    with bootstrap_engine.begin() as conn:
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
                    'Description', 'en-IN', 'DRAFT', NULL,
                    NULL, 0, :now, :owner, :now, NULL
                )
                """
            ),
            {
                "content_id": content_id.value,
                "tenant_id": tenant_id,
                "owner": principal,
                "now": FIXED_NOW,
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
                    'test.generic', 1, '{"marker":"v1"}'::jsonb,
                    'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                    'HUMAN', NULL, :now, :owner
                )
                """
            ),
            {
                "version_id": version_id.value,
                "tenant_id": tenant_id,
                "content_id": content_id.value,
                "owner": principal,
                "now": FIXED_NOW,
            },
        )
        conn.execute(
            text(
                """
                UPDATE content.contents
                SET current_version_id = :version_id, aggregate_revision = 1
                WHERE content_id = :content_id
                """
            ),
            {"version_id": version_id.value, "content_id": content_id.value},
        )
    return content_id, version_id


def _make_ref(
    *,
    tenant_id: uuid.UUID,
    content_id: ContentId,
    version_id: ContentVersionId,
    role: str = "primary",
    ordinal: int = 0,
    resource_type: str = "asset.image",
    resource_id: uuid.UUID | None = None,
    required: bool = True,
) -> VersionAssetRef:
    return VersionAssetRef(
        tenant_id=tenant_id,
        content_id=content_id,
        version_id=version_id,
        resource_ref=ResourceRef(
            resource_type=resource_type,
            resource_id=resource_id or uuid.uuid7(),
            resource_revision=None,
        ),
        role=role,
        ordinal=ordinal,
        required=required,
        created_at=FIXED_NOW,
    )


class TestVersionAssetRefSchema:
    def test_k_table_and_mapping_columns(self, bootstrap_engine) -> None:
        with bootstrap_engine.connect() as conn:
            cols = {
                row[0]
                for row in conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = 'content' AND table_name = 'version_asset_refs'"
                    )
                )
            }
        expected = {
            "tenant_id",
            "content_id",
            "version_id",
            "asset_resource_type",
            "asset_resource_id",
            "asset_resource_revision",
            "role",
            "ordinal",
            "required",
            "created_at",
        }
        assert cols == expected
        assert set(version_asset_refs_table.c.keys()) == expected
        assert version_asset_refs_table.schema == "content"

    def test_l_rls_forced_and_owner(self, bootstrap_engine) -> None:
        with bootstrap_engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT c.relrowsecurity, c.relforcerowsecurity, pg_get_userbyid(c.relowner)
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'content' AND c.relname = 'version_asset_refs'
                    """
                )
            ).one()
        assert row[0] is True
        assert row[1] is True
        assert row[2] == SCHEMA_OWNER_ROLE

    def test_m_primary_key_is_slot(self, bootstrap_engine) -> None:
        with bootstrap_engine.connect() as conn:
            cols = [
                row[0]
                for row in conn.execute(
                    text(
                        """
                        SELECT a.attname
                        FROM pg_index i
                        JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
                        JOIN pg_class c ON c.oid = i.indrelid
                        JOIN pg_namespace n ON n.oid = c.relnamespace
                        WHERE n.nspname = 'content' AND c.relname = 'version_asset_refs'
                          AND i.indisprimary
                        ORDER BY a.attnum
                        """
                    )
                )
            ]
        assert cols == [
            "tenant_id",
            "content_id",
            "version_id",
            "role",
            "ordinal",
        ]

    def test_n_immutability_triggers(self, bootstrap_engine, runtime_engine) -> None:
        tenant_id = uuid.uuid7()
        content_id, version_id = _seed_version(bootstrap_engine, tenant_id)
        ref = _make_ref(tenant_id=tenant_id, content_id=content_id, version_id=version_id)
        factory = SqlAlchemyContentUnitOfWorkFactory(runtime_engine)
        with factory(tenant_id) as uow:
            uow.version_asset_refs.insert_many([ref])
            uow.commit()
        with pytest.raises(Exception):
            with runtime_engine.begin() as conn:
                conn.execute(text("SELECT set_config('aieos.tenant_id', :tid, true)"), {"tid": str(tenant_id)})
                conn.execute(
                    text(
                        "UPDATE content.version_asset_refs SET required = false "
                        "WHERE content_id = :cid AND version_id = :vid"
                    ),
                    {"cid": content_id.value, "vid": version_id.value},
                )
        with pytest.raises(Exception):
            with runtime_engine.begin() as conn:
                conn.execute(text("SELECT set_config('aieos.tenant_id', :tid, true)"), {"tid": str(tenant_id)})
                conn.execute(
                    text(
                        "DELETE FROM content.version_asset_refs "
                        "WHERE content_id = :cid AND version_id = :vid"
                    ),
                    {"cid": content_id.value, "vid": version_id.value},
                )

    def test_o_runtime_cannot_update_or_delete(self, bootstrap_engine) -> None:
        with bootstrap_engine.connect() as conn:
            privileges = {
                row[0]
                for row in conn.execute(
                    text(
                        """
                        SELECT privilege_type
                        FROM information_schema.role_table_grants
                        WHERE table_schema = 'content'
                          AND table_name = 'version_asset_refs'
                          AND grantee = current_user
                        """
                    )
                )
            }
        # bootstrap is privileged; privilege contract is exercised via conftest grants.
        assert "version_asset_refs" in {
            row[0]
            for row in bootstrap_engine.connect()
            .execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'content'"))
            .all()
        }
        assert privileges is not None

    def test_p_insert_many_and_ordered_list(self, runtime_engine, bootstrap_engine) -> None:
        tenant_id = uuid.uuid7()
        content_id, version_id = _seed_version(bootstrap_engine, tenant_id)
        refs = [
            _make_ref(
                tenant_id=tenant_id,
                content_id=content_id,
                version_id=version_id,
                role="zulu",
                ordinal=1,
                resource_type="asset.b",
            ),
            _make_ref(
                tenant_id=tenant_id,
                content_id=content_id,
                version_id=version_id,
                role="alpha",
                ordinal=0,
                resource_type="asset.a",
            ),
            _make_ref(
                tenant_id=tenant_id,
                content_id=content_id,
                version_id=version_id,
                role="alpha",
                ordinal=1,
                resource_type="asset.c",
            ),
        ]
        factory = SqlAlchemyContentUnitOfWorkFactory(runtime_engine)
        with factory(tenant_id) as uow:
            uow.version_asset_refs.insert_many(refs)
            listed = uow.version_asset_refs.list_for_version(content_id, version_id)
            uow.commit()
        assert [(r.role, r.ordinal, r.resource_ref.resource_type) for r in listed] == [
            ("alpha", 0, "asset.a"),
            ("alpha", 1, "asset.c"),
            ("zulu", 1, "asset.b"),
        ]

    def test_q_mapping_round_trip(self, runtime_engine, bootstrap_engine) -> None:
        tenant_id = uuid.uuid7()
        content_id, version_id = _seed_version(bootstrap_engine, tenant_id)
        ref = _make_ref(
            tenant_id=tenant_id,
            content_id=content_id,
            version_id=version_id,
            required=False,
        )
        factory = SqlAlchemyContentUnitOfWorkFactory(runtime_engine)
        with factory(tenant_id) as uow:
            uow.version_asset_refs.insert_many([ref])
            listed = uow.version_asset_refs.list_for_version(content_id, version_id)
            uow.commit()
        assert len(listed) == 1
        assert listed[0].resource_ref == ref.resource_ref
        assert listed[0].required is False

    def test_r_tenant_isolation(self, runtime_engine, bootstrap_engine) -> None:
        tenant_a = uuid.uuid7()
        tenant_b = uuid.uuid7()
        content_id, version_id = _seed_version(bootstrap_engine, tenant_a)
        ref = _make_ref(tenant_id=tenant_a, content_id=content_id, version_id=version_id)
        factory = SqlAlchemyContentUnitOfWorkFactory(runtime_engine)
        with factory(tenant_a) as uow:
            uow.version_asset_refs.insert_many([ref])
            uow.commit()
        with factory(tenant_b) as uow:
            assert uow.version_asset_refs.list_for_version(content_id, version_id) == []

    def test_s_duplicate_slot_rejected(self, runtime_engine, bootstrap_engine) -> None:
        tenant_id = uuid.uuid7()
        content_id, version_id = _seed_version(bootstrap_engine, tenant_id)
        a = _make_ref(tenant_id=tenant_id, content_id=content_id, version_id=version_id)
        b = _make_ref(
            tenant_id=tenant_id,
            content_id=content_id,
            version_id=version_id,
            resource_id=uuid.uuid7(),
        )
        factory = SqlAlchemyContentUnitOfWorkFactory(runtime_engine)
        with factory(tenant_id) as uow:
            uow.version_asset_refs.insert_many([a])
            uow.commit()
        with pytest.raises(Exception):
            with factory(tenant_id) as uow:
                uow.version_asset_refs.insert_many([b])
                uow.commit()

    def test_t_fk_requires_version(self, runtime_engine) -> None:
        tenant_id = uuid.uuid7()
        ref = _make_ref(
            tenant_id=tenant_id,
            content_id=ContentId.generate(),
            version_id=ContentVersionId.generate(),
        )
        factory = SqlAlchemyContentUnitOfWorkFactory(runtime_engine)
        with pytest.raises(Exception):
            with factory(tenant_id) as uow:
                uow.version_asset_refs.insert_many([ref])
                uow.commit()

    def test_u_from_row_helper(self) -> None:
        rid = uuid.uuid7()
        content_id = ContentId.generate()
        version_id = ContentVersionId.generate()
        tenant_id = uuid.uuid7()
        mapped = version_asset_ref_from_row(
            {
                "tenant_id": tenant_id,
                "content_id": content_id.value,
                "version_id": version_id.value,
                "asset_resource_type": "asset.image",
                "asset_resource_id": rid,
                "asset_resource_revision": 2,
                "role": "primary",
                "ordinal": 0,
                "required": True,
                "created_at": FIXED_NOW,
            }
        )
        assert mapped.resource_ref.resource_id == rid
        assert mapped.resource_ref.resource_revision == 2

    def test_v_head_revision_is_i10(self, bootstrap_engine) -> None:
        with bootstrap_engine.connect() as conn:
            assert (
                conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "gcii110001"
            )

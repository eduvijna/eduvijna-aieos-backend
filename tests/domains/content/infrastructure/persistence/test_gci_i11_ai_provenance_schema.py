"""GCI-I11 AI provenance DB validator and migration chain."""

from __future__ import annotations

import io
import uuid
from contextlib import redirect_stdout
from datetime import UTC, datetime

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.domains.content.domain.provenance import (
    AIGenerationProvenanceV1,
    ai_generation_provenance_as_json,
)
from aieos.platform.resources import ResourceRef
from tests.conftest import SCHEMA_OWNER_ROLE, alembic_config, provision_runtime_grants
from tests.dbutil import REPO_ROOT, clear_asset_audit_rows_for_schema_downgrade

pytestmark = pytest.mark.gci_i11

FIXED_NOW = datetime(2026, 8, 14, 20, 30, tzinfo=UTC)


def _canonical_provenance() -> dict:
    return ai_generation_provenance_as_json(
        AIGenerationProvenanceV1(
            generation_run_ref=ResourceRef("generation.run", uuid.uuid7(), None),
            prompt_execution_ref=None,
            provider_id="test.provider",
            model_id="neutral-model",
            capability_id="content.generate.lesson",
            source_refs=(),
            policy_refs=(),
            evaluation_refs=(),
            correlation_id=uuid.uuid7(),
        )
    )


def _seed_content(bootstrap_engine: Engine, tenant_id: uuid.UUID) -> uuid.UUID:
    content_id = uuid.uuid7()
    owner = uuid.uuid7()
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
                "content_id": content_id,
                "tenant_id": tenant_id,
                "owner": owner,
                "now": FIXED_NOW,
            },
        )
    return content_id


def _insert_ai_version(
    bootstrap_engine: Engine,
    *,
    tenant_id: uuid.UUID,
    content_id: uuid.UUID,
    provenance: dict | None,
) -> None:
    with bootstrap_engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO content.content_versions (
                    version_id, tenant_id, content_id, version_number, parent_version_id,
                    schema_id, schema_version, payload, payload_sha256, origin,
                    provenance, created_at, created_by_principal_id
                ) VALUES (
                    :vid, :tid, :cid, 1, NULL,
                    'test.generic', 1, '{"marker":"v1"}'::jsonb,
                    repeat('a', 64), 'AI',
                    CAST(:prov AS jsonb), :now, :actor
                )
                """
            ),
            {
                "vid": uuid.uuid7(),
                "tid": tenant_id,
                "cid": content_id,
                "prov": None if provenance is None else __import__("json").dumps(provenance),
                "now": FIXED_NOW,
                "actor": uuid.uuid7(),
            },
        )


class TestAIProvenanceDbConstraint:
    def test_valid_canonical_insert_accepted(self, bootstrap_engine) -> None:
        tenant_id = uuid.uuid7()
        content_id = _seed_content(bootstrap_engine, tenant_id)
        _insert_ai_version(
            bootstrap_engine,
            tenant_id=tenant_id,
            content_id=content_id,
            provenance=_canonical_provenance(),
        )

    def test_empty_object_rejected(self, bootstrap_engine) -> None:
        tenant_id = uuid.uuid7()
        content_id = _seed_content(bootstrap_engine, tenant_id)
        with pytest.raises(Exception):
            _insert_ai_version(
                bootstrap_engine,
                tenant_id=tenant_id,
                content_id=content_id,
                provenance={},
            )

    def test_unknown_key_rejected(self, bootstrap_engine) -> None:
        tenant_id = uuid.uuid7()
        content_id = _seed_content(bootstrap_engine, tenant_id)
        prov = _canonical_provenance()
        prov["api_key"] = "SECRET"
        with pytest.raises(Exception):
            _insert_ai_version(
                bootstrap_engine,
                tenant_id=tenant_id,
                content_id=content_id,
                provenance=prov,
            )

    def test_missing_generation_run_ref_rejected(self, bootstrap_engine) -> None:
        tenant_id = uuid.uuid7()
        content_id = _seed_content(bootstrap_engine, tenant_id)
        prov = _canonical_provenance()
        del prov["generation_run_ref"]
        with pytest.raises(Exception):
            _insert_ai_version(
                bootstrap_engine,
                tenant_id=tenant_id,
                content_id=content_id,
                provenance=prov,
            )

    def test_schema_version_boolean_rejected(self, bootstrap_engine) -> None:
        tenant_id = uuid.uuid7()
        content_id = _seed_content(bootstrap_engine, tenant_id)
        prov = _canonical_provenance()
        prov["schema_version"] = True
        with pytest.raises(Exception):
            _insert_ai_version(
                bootstrap_engine,
                tenant_id=tenant_id,
                content_id=content_id,
                provenance=prov,
            )

    def test_schema_version_float_rejected(self, bootstrap_engine) -> None:
        tenant_id = uuid.uuid7()
        content_id = _seed_content(bootstrap_engine, tenant_id)
        prov = _canonical_provenance()
        prov["schema_version"] = 1.0
        with pytest.raises(Exception):
            _insert_ai_version(
                bootstrap_engine,
                tenant_id=tenant_id,
                content_id=content_id,
                provenance=prov,
            )

    def test_human_row_unaffected(self, bootstrap_engine) -> None:
        tenant_id = uuid.uuid7()
        content_id = _seed_content(bootstrap_engine, tenant_id)
        with bootstrap_engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO content.content_versions (
                        version_id, tenant_id, content_id, version_number, parent_version_id,
                        schema_id, schema_version, payload, payload_sha256, origin,
                        provenance, created_at, created_by_principal_id
                    ) VALUES (
                        :vid, :tid, :cid, 1, NULL,
                        'test.generic', 1, '{"marker":"v1"}'::jsonb,
                        repeat('a', 64), 'HUMAN',
                        NULL, :now, :actor
                    )
                    """
                ),
                {
                    "vid": uuid.uuid7(),
                    "tid": tenant_id,
                    "cid": content_id,
                    "now": FIXED_NOW,
                    "actor": uuid.uuid7(),
                },
            )


class TestMigrationCycle:
    def test_upgrade_downgrade_to_i10_and_head(
        self, postgres18, bootstrap_engine
    ) -> None:
        cfg = alembic_config(postgres18["migrator_url"])
        clear_asset_audit_rows_for_schema_downgrade(bootstrap_engine)
        command.downgrade(cfg, "gcii100001")
        with bootstrap_engine.connect() as conn:
            assert (
                conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "gcii100001"
            )
            exists = conn.execute(
                text(
                    """
                    SELECT count(*) FROM pg_proc p
                    JOIN pg_namespace n ON n.oid = p.pronamespace
                    WHERE n.nspname = 'content'
                      AND p.proname = 'ai_generation_provenance_v1_is_valid'
                    """
                )
            ).scalar_one()
        assert int(exists) == 0
        command.upgrade(cfg, "head")
        provision_runtime_grants(bootstrap_engine)
        with bootstrap_engine.connect() as conn:
            assert (
                conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "pedi10b6001"
            )

    def test_offline_sql_assumes_owner_before_i11_ddl(self) -> None:
        cfg = alembic_config("postgresql+psycopg://offline-check/unused")
        output = io.StringIO()
        with redirect_stdout(output):
            command.upgrade(cfg, "gcii110001", sql=True)
        sql = output.getvalue()
        assert f"SET LOCAL ROLE {SCHEMA_OWNER_ROLE}" in sql
        assert sql.index(f"SET LOCAL ROLE {SCHEMA_OWNER_ROLE}") < sql.index(
            "ai_generation_provenance_v1_is_valid"
        )
        assert (
            REPO_ROOT / "migrations" / "versions" / "gcii110001_ai_provenance.py"
        ).is_file()

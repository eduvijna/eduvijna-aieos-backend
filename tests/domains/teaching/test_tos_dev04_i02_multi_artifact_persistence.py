"""TOS-DEV04-I02 multi-artifact provenance + GenerationRun fence PostgreSQL proofs."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.domains.content.application.errors import AIGenerationRunAlreadyMaterialized
from aieos.domains.content.domain.identities import ContentId, ContentVersionId, VersionNumber
from aieos.domains.content.domain.origin import ContentOrigin
from aieos.domains.content.domain.provenance import (
    AIGenerationProvenanceV1,
    AIGenerationProvenanceV2,
    ai_generation_provenance_as_json,
)
from aieos.domains.content.domain.schema import SchemaId, SchemaVersion
from aieos.domains.content.domain.version import (
    ContentPayload,
    ContentVersion,
    canonical_payload_json,
)
from aieos.domains.content.infrastructure.persistence.uow import (
    SqlAlchemyContentUnitOfWorkFactory,
)
from aieos.platform.ai.application.errors import GenerationRunConflict
from aieos.platform.ai.domain.generation_run import (
    GenerationRun,
    GenerationRunId,
    GenerationRunStatus,
)
from aieos.platform.ai.infrastructure.persistence.uow import (
    SqlAlchemyAIUnitOfWorkFactory,
)
from aieos.platform.idempotency.hashing import fingerprint_material, hash_idempotency_key
from aieos.platform.resources import ResourceRef
from aieos.platform.runtime.readiness import EXPECTED_ALEMBIC_HEAD
from tests.conftest import alembic_config, provision_runtime_grants
from tests.dbutil import REPO_ROOT
from tools.release.common import EXPECTED_MIGRATION_HEAD

pytestmark = pytest.mark.tos_dev04_i02

FIXED_NOW = datetime(2026, 8, 29, 9, 0, tzinfo=UTC)
ARTIFACT_KINDS = (
    "lesson_plan",
    "worksheet",
    "quiz",
    "homework",
    "answer_key",
    "teacher_notes",
)
CAP_WORKSHEET = "education.generate_worksheet"
CAP_PREP = "education.generate_preparation_kit"


@pytest.fixture(autouse=True)
def _cleanup_i02_shared_db_rows(postgres18: dict[str, str]) -> None:
    """Remove I02 V2 / multi-outcome rows that would block other suites' downgrades."""
    from sqlalchemy import create_engine

    yield
    engine = create_engine(postgres18["bootstrap_url"])
    try:
        _clear_i02_downgrade_blockers(engine)
    finally:
        engine.dispose()


def _clear_i02_downgrade_blockers(bootstrap_engine: Engine) -> None:
    """TEST-ONLY: remove V2 ContentVersions and GenerationRuns left by I02 proofs."""
    with bootstrap_engine.begin() as conn:
        conn.execute(text("DELETE FROM ai.generation_runs"))
        conn.execute(
            text(
                """
                UPDATE content.contents AS c
                   SET current_version_id = NULL,
                       published_version_id = NULL
                 WHERE EXISTS (
                    SELECT 1
                      FROM content.content_versions AS v
                     WHERE v.content_id = c.content_id
                       AND v.origin = 'AI'
                       AND v.provenance IS NOT NULL
                       AND (v.provenance->>'schema_version') = '2'
                 )
                """
            )
        )
        conn.execute(
            text(
                "ALTER TABLE content.review_decisions "
                "DISABLE TRIGGER review_decisions_immutable_delete"
            )
        )
        try:
            for table in (
                "content.version_asset_refs",
                "content.review_decisions",
                "content.publications",
            ):
                conn.execute(
                    text(
                        f"""
                        DELETE FROM {table} AS child
                         WHERE EXISTS (
                            SELECT 1
                              FROM content.content_versions AS v
                             WHERE v.version_id = child.version_id
                               AND v.origin = 'AI'
                               AND v.provenance IS NOT NULL
                               AND (v.provenance->>'schema_version') = '2'
                         )
                         OR EXISTS (
                            SELECT 1
                              FROM content.content_versions AS descendant
                              JOIN content.content_versions AS parent
                                ON parent.tenant_id = descendant.tenant_id
                               AND parent.content_id = descendant.content_id
                               AND parent.version_id = descendant.parent_version_id
                             WHERE descendant.version_id = child.version_id
                               AND parent.origin = 'AI'
                               AND parent.provenance IS NOT NULL
                               AND (parent.provenance->>'schema_version') = '2'
                         )
                        """
                    )
                )
        finally:
            conn.execute(
                text(
                    "ALTER TABLE content.review_decisions "
                    "ENABLE TRIGGER review_decisions_immutable_delete"
                )
            )
        conn.execute(
            text(
                "ALTER TABLE content.content_versions "
                "DISABLE TRIGGER content_versions_immutable_delete"
            )
        )
        # Child ContentVersions that parent to AI V2 must be removed first
        # (append-after-approve leaves HUMAN descendants that RESTRICT parents).
        conn.execute(
            text(
                """
                DELETE FROM content.content_versions AS child
                 WHERE child.parent_version_id IS NOT NULL
                   AND EXISTS (
                    SELECT 1
                      FROM content.content_versions AS parent
                     WHERE parent.tenant_id = child.tenant_id
                       AND parent.content_id = child.content_id
                       AND parent.version_id = child.parent_version_id
                       AND parent.origin = 'AI'
                       AND parent.provenance IS NOT NULL
                       AND (parent.provenance->>'schema_version') = '2'
                   )
                """
            )
        )
        conn.execute(
            text(
                """
                DELETE FROM content.content_versions
                 WHERE origin = 'AI'
                   AND provenance IS NOT NULL
                   AND (provenance->>'schema_version') = '2'
                """
            )
        )
        conn.execute(
            text(
                "ALTER TABLE content.content_versions "
                "ENABLE TRIGGER content_versions_immutable_delete"
            )
        )


def _clear_ai_versions_for_migration_cycle(bootstrap_engine: Engine) -> None:
    """TEST-ONLY: reset AI versions/runs for migration cycle isolation."""
    _clear_i02_downgrade_blockers(bootstrap_engine)
    with bootstrap_engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE content.contents AS c
                   SET current_version_id = NULL,
                       published_version_id = NULL
                 WHERE EXISTS (
                    SELECT 1
                      FROM content.content_versions AS v
                     WHERE v.content_id = c.content_id
                       AND v.origin = 'AI'
                 )
                """
            )
        )
        conn.execute(
            text(
                "ALTER TABLE content.review_decisions "
                "DISABLE TRIGGER review_decisions_immutable_delete"
            )
        )
        try:
            for table in (
                "content.version_asset_refs",
                "content.review_decisions",
                "content.publications",
            ):
                conn.execute(
                    text(
                        f"""
                        DELETE FROM {table} AS child
                         WHERE EXISTS (
                            SELECT 1
                              FROM content.content_versions AS v
                             WHERE v.version_id = child.version_id
                               AND v.origin = 'AI'
                         )
                        """
                    )
                )
        finally:
            conn.execute(
                text(
                    "ALTER TABLE content.review_decisions "
                    "ENABLE TRIGGER review_decisions_immutable_delete"
                )
            )
        conn.execute(
            text(
                "ALTER TABLE content.content_versions "
                "DISABLE TRIGGER content_versions_immutable_delete"
            )
        )
        conn.execute(
            text(
                """
                DELETE FROM content.content_versions
                 WHERE origin = 'AI'
                """
            )
        )
        conn.execute(
            text(
                "ALTER TABLE content.content_versions "
                "ENABLE TRIGGER content_versions_immutable_delete"
            )
        )


def _ensure_head(postgres18, bootstrap_engine: Engine) -> None:
    cfg = alembic_config(postgres18["migrator_url"])
    command.upgrade(cfg, "head")
    provision_runtime_grants(bootstrap_engine)


def _run(
    *,
    tenant_id: uuid.UUID,
    principal_id: uuid.UUID,
    work_id: uuid.UUID | None = None,
    work_revision: int = 0,
    capability_id: str = CAP_WORKSHEET,
    status: GenerationRunStatus = GenerationRunStatus.RUNNING,
    key: str = "key-1",
    provider_id: str = "fake",
    model_id: str = "fake-model",
    lease_expires_at: datetime | None = None,
) -> GenerationRun:
    if status is GenerationRunStatus.RUNNING and lease_expires_at is None:
        lease_expires_at = FIXED_NOW + timedelta(seconds=120)
    return GenerationRun(
        generation_run_id=GenerationRunId.generate(),
        tenant_id=tenant_id,
        principal_id=principal_id,
        work_resource_type="teaching.work",
        work_resource_id=work_id or uuid.uuid7(),
        work_resource_revision=work_revision,
        capability_id=capability_id,
        provider_id=provider_id,
        model_id=model_id,
        status=status,
        request_fingerprint_sha256=fingerprint_material(
            {"work_id": str(work_id), "rev": work_revision, "cap": capability_id}
        ),
        idempotency_key_sha256=hash_idempotency_key(key),
        provider_response_id=None,
        input_tokens=None,
        output_tokens=None,
        total_tokens=None,
        educational_quality_summary=None,
        result_content_id=None,
        result_version_id=None,
        result_content_revision=None,
        failure_code=None,
        aggregate_revision=0,
        created_at=FIXED_NOW,
        updated_at=FIXED_NOW,
        completed_at=None if status is GenerationRunStatus.RUNNING else FIXED_NOW,
        lease_expires_at=lease_expires_at,
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


def _v1_provenance(run_id: uuid.UUID) -> dict:
    return ai_generation_provenance_as_json(
        AIGenerationProvenanceV1(
            generation_run_ref=ResourceRef("generation.run", run_id, None),
            prompt_execution_ref=None,
            provider_id="test.provider",
            model_id="neutral-model",
            capability_id=CAP_WORKSHEET,
            source_refs=(),
            policy_refs=(),
            evaluation_refs=(),
            correlation_id=uuid.uuid7(),
        )
    )


def _v2_provenance(run_id: uuid.UUID, artifact_kind: str) -> dict:
    return ai_generation_provenance_as_json(
        AIGenerationProvenanceV2(
            generation_run_ref=ResourceRef("generation.run", run_id, None),
            prompt_execution_ref=None,
            provider_id="test.provider",
            model_id="neutral-model",
            capability_id=CAP_PREP,
            source_refs=(),
            policy_refs=(),
            evaluation_refs=(),
            correlation_id=uuid.uuid7(),
            artifact_kind=artifact_kind,
        )
    )


def _insert_ai_version(
    bootstrap_engine: Engine,
    *,
    tenant_id: uuid.UUID,
    content_id: uuid.UUID,
    provenance: dict,
    version_number: int = 1,
    created_at: datetime | None = None,
    version_id: uuid.UUID | None = None,
) -> uuid.UUID:
    vid = version_id or uuid.uuid7()
    payload = ContentPayload.from_mapping({"marker": "i02"})
    with bootstrap_engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO content.content_versions (
                    version_id, tenant_id, content_id, version_number, parent_version_id,
                    schema_id, schema_version, payload, payload_sha256, origin,
                    provenance, created_at, created_by_principal_id
                ) VALUES (
                    :vid, :tid, :cid, :vnum, NULL,
                    'test.generic', 1, CAST(:payload AS jsonb),
                    :sha, 'AI',
                    CAST(:prov AS jsonb), :now, :actor
                )
                """
            ),
            {
                "vid": vid,
                "tid": tenant_id,
                "cid": content_id,
                "vnum": version_number,
                "payload": canonical_payload_json(payload.body),
                "sha": payload.sha256.value,
                "prov": json.dumps(provenance),
                "now": created_at or FIXED_NOW,
                "actor": uuid.uuid7(),
            },
        )
    return vid


def _make_domain_version(
    *,
    tenant_id: uuid.UUID,
    content_id: ContentId,
    version_number: int = 1,
) -> ContentVersion:
    return ContentVersion(
        version_id=ContentVersionId.generate(),
        tenant_id=tenant_id,
        content_id=content_id,
        version_number=VersionNumber(version_number),
        parent_version_id=None,
        schema_id=SchemaId("test.generic"),
        schema_version=SchemaVersion(1),
        payload=ContentPayload.from_mapping({"marker": "i02"}),
        origin=ContentOrigin.AI,
        created_at=FIXED_NOW,
        created_by_principal_id=uuid.uuid7(),
    )


class TestMigrationHeadAndSchema:
    def test_single_alembic_head_is_tosd060001(self, bootstrap_engine: Engine) -> None:
        assert EXPECTED_ALEMBIC_HEAD == "tosd080001"
        assert EXPECTED_MIGRATION_HEAD == "tosd080001"
        with bootstrap_engine.connect() as conn:
            assert (
                conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "tosd080001"
            )
        versions = sorted(
            p.name
            for p in (REPO_ROOT / "migrations" / "versions").glob("*.py")
            if p.name != "__pycache__"
        )
        assert versions[-1].startswith("tosd080001_")

    def test_new_indexes_and_constraint_present(
        self, bootstrap_engine: Engine
    ) -> None:
        with bootstrap_engine.connect() as conn:
            for schema, name in (
                ("ai", "uq_ai_generation_runs_work_revision_capability_outcome"),
                ("ai", "uq_ai_generation_runs_work_capability_running"),
                ("content", "uq_content_versions_ai_generation_run_id_v1"),
                ("content", "uq_content_versions_ai_generation_run_artifact_v2"),
            ):
                found = conn.execute(
                    text(
                        """
                        SELECT 1 FROM pg_indexes
                        WHERE schemaname = :schema AND indexname = :name
                        """
                    ),
                    {"schema": schema, "name": name},
                ).scalar_one_or_none()
                assert found == 1, name
            for schema, name in (
                ("ai", "uq_ai_generation_runs_work_active_or_succeeded"),
                ("content", "uq_content_versions_ai_generation_run_id"),
            ):
                found = conn.execute(
                    text(
                        """
                        SELECT 1 FROM pg_indexes
                        WHERE schemaname = :schema AND indexname = :name
                        """
                    ),
                    {"schema": schema, "name": name},
                ).scalar_one_or_none()
                assert found is None, name
            ck = conn.execute(
                text(
                    """
                    SELECT 1 FROM pg_constraint
                    WHERE conname = 'ck_content_versions_ai_provenance'
                    """
                )
            ).scalar_one_or_none()
            assert ck == 1
            old_ck = conn.execute(
                text(
                    """
                    SELECT 1 FROM pg_constraint
                    WHERE conname = 'ck_content_versions_ai_provenance_v1'
                    """
                )
            ).scalar_one_or_none()
            assert old_ck is None

    def test_upgrade_from_tosd030002_preserves_v1(
        self, postgres18, bootstrap_engine: Engine
    ) -> None:
        cfg = alembic_config(postgres18["migrator_url"])
        tenant_id = uuid.uuid7()
        run_id = uuid.uuid7()
        content_id = _seed_content(bootstrap_engine, tenant_id)
        _clear_ai_versions_for_migration_cycle(bootstrap_engine)
        try:
            command.downgrade(cfg, "tosd030002")
            with bootstrap_engine.begin() as conn:
                assert (
                    conn.execute(
                        text("SELECT version_num FROM alembic_version")
                    ).scalar_one()
                    == "tosd030002"
                )
                conn.execute(
                    text(
                        """
                        INSERT INTO content.content_versions (
                            version_id, tenant_id, content_id, version_number,
                            parent_version_id, schema_id, schema_version, payload,
                            payload_sha256, origin, provenance, created_at,
                            created_by_principal_id
                        ) VALUES (
                            :vid, :tid, :cid, 1, NULL,
                            'test.generic', 1, '{"marker":"v1-preserve"}'::jsonb,
                            repeat('b', 64), 'AI',
                            CAST(:prov AS jsonb), :now, :actor
                        )
                        """
                    ),
                    {
                        "vid": uuid.uuid7(),
                        "tid": tenant_id,
                        "cid": content_id,
                        "prov": json.dumps(_v1_provenance(run_id)),
                        "now": FIXED_NOW,
                        "actor": uuid.uuid7(),
                    },
                )
            command.upgrade(cfg, "head")
            provision_runtime_grants(bootstrap_engine)
            with bootstrap_engine.connect() as conn:
                assert (
                    conn.execute(
                        text("SELECT version_num FROM alembic_version")
                    ).scalar_one()
                    == "tosd080001"
                )
                prov = conn.execute(
                    text(
                        "SELECT provenance FROM content.content_versions "
                        "WHERE tenant_id = :tid"
                    ),
                    {"tid": tenant_id},
                ).scalar_one()
                assert prov["schema_version"] == 1
                assert prov["generation_run_ref"]["resource_id"] == str(run_id)
                assert "artifact_kind" not in prov
        finally:
            _ensure_head(postgres18, bootstrap_engine)

    def test_baseline_downgrade_roundtrip(
        self, postgres18, bootstrap_engine: Engine
    ) -> None:
        cfg = alembic_config(postgres18["migrator_url"])
        _clear_ai_versions_for_migration_cycle(bootstrap_engine)
        try:
            command.downgrade(cfg, "tosd030002")
            with bootstrap_engine.connect() as conn:
                assert (
                    conn.execute(
                        text("SELECT version_num FROM alembic_version")
                    ).scalar_one()
                    == "tosd030002"
                )
            command.upgrade(cfg, "head")
            provision_runtime_grants(bootstrap_engine)
            with bootstrap_engine.connect() as conn:
                assert (
                    conn.execute(
                        text("SELECT version_num FROM alembic_version")
                    ).scalar_one()
                    == "tosd080001"
                )
        finally:
            _ensure_head(postgres18, bootstrap_engine)

    def test_downgrade_fails_closed_with_v2_rows(
        self, postgres18, bootstrap_engine: Engine
    ) -> None:
        cfg = alembic_config(postgres18["migrator_url"])
        tenant_id = uuid.uuid7()
        content_id = _seed_content(bootstrap_engine, tenant_id)
        _insert_ai_version(
            bootstrap_engine,
            tenant_id=tenant_id,
            content_id=content_id,
            provenance=_v2_provenance(uuid.uuid7(), "worksheet"),
        )
        try:
            with pytest.raises(Exception, match="refuse downgrade"):
                command.downgrade(cfg, "tosd030002")
            with bootstrap_engine.connect() as conn:
                assert (
                    conn.execute(
                        text("SELECT version_num FROM alembic_version")
                    ).scalar_one()
                    == "tosd080001"
                )
                count = conn.execute(
                    text(
                        "SELECT count(*) FROM content.content_versions "
                        "WHERE tenant_id = :tid"
                    ),
                    {"tid": tenant_id},
                ).scalar_one()
                assert int(count) == 1
        finally:
            _ensure_head(postgres18, bootstrap_engine)
            _clear_ai_versions_for_migration_cycle(bootstrap_engine)


class TestProvenanceValidation:
    def test_v1_and_v2_accepted(self, bootstrap_engine: Engine) -> None:
        tenant_id = uuid.uuid7()
        content_id = _seed_content(bootstrap_engine, tenant_id)
        _insert_ai_version(
            bootstrap_engine,
            tenant_id=tenant_id,
            content_id=content_id,
            provenance=_v1_provenance(uuid.uuid7()),
        )
        content_id_2 = _seed_content(bootstrap_engine, tenant_id)
        _insert_ai_version(
            bootstrap_engine,
            tenant_id=tenant_id,
            content_id=content_id_2,
            provenance=_v2_provenance(uuid.uuid7(), "lesson_plan"),
        )

    def test_malformed_rejected(self, bootstrap_engine: Engine) -> None:
        tenant_id = uuid.uuid7()
        content_id = _seed_content(bootstrap_engine, tenant_id)
        run_id = uuid.uuid7()

        missing_kind = _v2_provenance(run_id, "worksheet")
        del missing_kind["artifact_kind"]
        with pytest.raises(Exception):
            _insert_ai_version(
                bootstrap_engine,
                tenant_id=tenant_id,
                content_id=content_id,
                provenance=missing_kind,
            )

        content_id = _seed_content(bootstrap_engine, tenant_id)
        extra = _v2_provenance(run_id, "worksheet")
        extra["api_key"] = "SECRET"
        with pytest.raises(Exception):
            _insert_ai_version(
                bootstrap_engine,
                tenant_id=tenant_id,
                content_id=content_id,
                provenance=extra,
            )

        content_id = _seed_content(bootstrap_engine, tenant_id)
        bad_sv = _v2_provenance(run_id, "worksheet")
        bad_sv["schema_version"] = 3
        # schema_version 3 also fails exact-key / dispatcher
        with pytest.raises(Exception):
            _insert_ai_version(
                bootstrap_engine,
                tenant_id=tenant_id,
                content_id=content_id,
                provenance=bad_sv,
            )

        content_id = _seed_content(bootstrap_engine, tenant_id)
        v1_with_kind = _v1_provenance(run_id)
        v1_with_kind["artifact_kind"] = "worksheet"
        with pytest.raises(Exception):
            _insert_ai_version(
                bootstrap_engine,
                tenant_id=tenant_id,
                content_id=content_id,
                provenance=v1_with_kind,
            )

        content_id = _seed_content(bootstrap_engine, tenant_id)
        bad_kind = _v2_provenance(run_id, "worksheet")
        bad_kind["artifact_kind"] = "Worksheet"
        with pytest.raises(Exception):
            _insert_ai_version(
                bootstrap_engine,
                tenant_id=tenant_id,
                content_id=content_id,
                provenance=bad_kind,
            )


class TestContentUniquenessAndQueries:
    def test_v1_unique_and_singular_query(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        run_id = uuid.uuid7()
        content_id = _seed_content(bootstrap_engine, tenant_id)
        vid = _insert_ai_version(
            bootstrap_engine,
            tenant_id=tenant_id,
            content_id=content_id,
            provenance=_v1_provenance(run_id),
        )
        content_id_2 = _seed_content(bootstrap_engine, tenant_id)
        with pytest.raises(Exception):
            _insert_ai_version(
                bootstrap_engine,
                tenant_id=tenant_id,
                content_id=content_id_2,
                provenance=_v1_provenance(run_id),
            )

        factory = SqlAlchemyContentUnitOfWorkFactory(runtime_engine)
        with factory(tenant_id) as uow:
            found = uow.versions.find_by_generation_run_id(run_id)
            assert found is not None
            assert found.version_id.value == vid
            plural = uow.versions.find_all_by_generation_run_id(run_id)
            assert len(plural) == 1
            assert isinstance(plural[0].provenance, AIGenerationProvenanceV1)

    def test_v2_six_artifacts_plural_and_singular_safety(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        other_tenant = uuid.uuid7()
        run_id = uuid.uuid7()
        version_ids: list[uuid.UUID] = []
        for index, kind in enumerate(ARTIFACT_KINDS):
            content_id = _seed_content(bootstrap_engine, tenant_id)
            vid = _insert_ai_version(
                bootstrap_engine,
                tenant_id=tenant_id,
                content_id=content_id,
                provenance=_v2_provenance(run_id, kind),
                created_at=FIXED_NOW + timedelta(seconds=index),
            )
            version_ids.append(vid)

        # Duplicate artifact_kind rejected.
        content_id = _seed_content(bootstrap_engine, tenant_id)
        with pytest.raises(Exception):
            _insert_ai_version(
                bootstrap_engine,
                tenant_id=tenant_id,
                content_id=content_id,
                provenance=_v2_provenance(run_id, "worksheet"),
            )

        # Cross-tenant isolation of uniqueness + queries.
        other_content = _seed_content(bootstrap_engine, other_tenant)
        _insert_ai_version(
            bootstrap_engine,
            tenant_id=other_tenant,
            content_id=other_content,
            provenance=_v2_provenance(run_id, "worksheet"),
        )

        factory = SqlAlchemyContentUnitOfWorkFactory(runtime_engine)
        with factory(tenant_id) as uow:
            singular = uow.versions.find_by_generation_run_id(run_id)
            assert singular is None  # V1-only helper must not collapse V2 set
            plural = uow.versions.find_all_by_generation_run_id(run_id)
            assert len(plural) == 6
            kinds = [b.artifact_kind for b in plural]
            assert kinds == list(ARTIFACT_KINDS)
            assert [b.version.version_id.value for b in plural] == version_ids
            for binding in plural:
                assert isinstance(binding.provenance, AIGenerationProvenanceV2)
                assert binding.generation_run_id == run_id

        with factory(other_tenant) as uow:
            other_plural = uow.versions.find_all_by_generation_run_id(run_id)
            assert len(other_plural) == 1
            assert other_plural[0].artifact_kind == "worksheet"

    def test_unique_conflict_translates_to_application_error(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        run_id = uuid.uuid7()
        content_id = ContentId(_seed_content(bootstrap_engine, tenant_id))
        factory = SqlAlchemyContentUnitOfWorkFactory(runtime_engine)
        provenance = AIGenerationProvenanceV1(
            generation_run_ref=ResourceRef("generation.run", run_id, None),
            prompt_execution_ref=None,
            provider_id="test.provider",
            model_id="neutral-model",
            capability_id=CAP_WORKSHEET,
            source_refs=(),
            policy_refs=(),
            evaluation_refs=(),
            correlation_id=uuid.uuid7(),
        )
        with factory(tenant_id) as uow:
            uow.versions.insert(
                _make_domain_version(tenant_id=tenant_id, content_id=content_id),
                ai_generation_provenance_as_json(provenance),
            )
            uow.commit()
        content_id_2 = ContentId(_seed_content(bootstrap_engine, tenant_id))
        with factory(tenant_id) as uow:
            with pytest.raises(AIGenerationRunAlreadyMaterialized):
                uow.versions.insert(
                    _make_domain_version(tenant_id=tenant_id, content_id=content_id_2),
                    ai_generation_provenance_as_json(provenance),
                )
                uow.commit()


class TestGenerationRunFences:
    def test_fence_a_same_revision_capability_blocks(
        self, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        work_id = uuid.uuid7()
        factory = SqlAlchemyAIUnitOfWorkFactory(runtime_engine)
        first = _run(
            tenant_id=tenant_id,
            principal_id=principal_id,
            work_id=work_id,
            work_revision=0,
            status=GenerationRunStatus.SUCCEEDED,
            key="a1",
            lease_expires_at=None,
        )
        with factory(tenant_id) as uow:
            uow.generation_runs.insert(first)
            uow.commit()
        second = _run(
            tenant_id=tenant_id,
            principal_id=principal_id,
            work_id=work_id,
            work_revision=0,
            key="a2",
        )
        with factory(tenant_id) as uow:
            with pytest.raises(GenerationRunConflict):
                uow.generation_runs.insert(second)
                uow.commit()

    def test_fence_a_allows_different_revision(
        self, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        work_id = uuid.uuid7()
        factory = SqlAlchemyAIUnitOfWorkFactory(runtime_engine)
        r0 = _run(
            tenant_id=tenant_id,
            principal_id=principal_id,
            work_id=work_id,
            work_revision=0,
            status=GenerationRunStatus.SUCCEEDED,
            key="b0",
            lease_expires_at=None,
        )
        r1 = _run(
            tenant_id=tenant_id,
            principal_id=principal_id,
            work_id=work_id,
            work_revision=1,
            status=GenerationRunStatus.SUCCEEDED,
            key="b1",
            lease_expires_at=None,
        )
        with factory(tenant_id) as uow:
            uow.generation_runs.insert(r0)
            uow.generation_runs.insert(r1)
            uow.commit()

    def test_capability_coexistence_same_revision(
        self, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        work_id = uuid.uuid7()
        factory = SqlAlchemyAIUnitOfWorkFactory(runtime_engine)
        worksheet = _run(
            tenant_id=tenant_id,
            principal_id=principal_id,
            work_id=work_id,
            capability_id=CAP_WORKSHEET,
            status=GenerationRunStatus.SUCCEEDED,
            key="c-ws",
            lease_expires_at=None,
        )
        prep = _run(
            tenant_id=tenant_id,
            principal_id=principal_id,
            work_id=work_id,
            capability_id=CAP_PREP,
            status=GenerationRunStatus.SUCCEEDED,
            key="c-prep",
            lease_expires_at=None,
        )
        with factory(tenant_id) as uow:
            uow.generation_runs.insert(worksheet)
            uow.generation_runs.insert(prep)
            uow.commit()
        with factory(tenant_id) as uow:
            outcome_ws = uow.generation_runs.find_outcome_for_work_revision_capability(
                work_resource_id=work_id,
                work_resource_revision=0,
                capability_id=CAP_WORKSHEET,
            )
            outcome_prep = uow.generation_runs.find_outcome_for_work_revision_capability(
                work_resource_id=work_id,
                work_resource_revision=0,
                capability_id=CAP_PREP,
            )
            assert outcome_ws is not None
            assert outcome_prep is not None
            assert outcome_ws.generation_run_id != outcome_prep.generation_run_id

    def test_fence_b_blocks_cross_revision_running(
        self, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        work_id = uuid.uuid7()
        factory = SqlAlchemyAIUnitOfWorkFactory(runtime_engine)
        r0 = _run(
            tenant_id=tenant_id,
            principal_id=principal_id,
            work_id=work_id,
            work_revision=0,
            key="d0",
        )
        with factory(tenant_id) as uow:
            uow.generation_runs.insert(r0)
            uow.commit()
        r1 = _run(
            tenant_id=tenant_id,
            principal_id=principal_id,
            work_id=work_id,
            work_revision=1,
            key="d1",
        )
        with factory(tenant_id) as uow:
            with pytest.raises(GenerationRunConflict):
                uow.generation_runs.insert(r1)
                uow.commit()
        with factory(tenant_id) as uow:
            running = uow.generation_runs.find_running_for_work_capability(
                work_resource_id=work_id,
                capability_id=CAP_WORKSHEET,
            )
            assert running is not None
            assert running.work_resource_revision == 0

    def test_failed_releases_fence_a(self, runtime_engine: Engine) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        work_id = uuid.uuid7()
        factory = SqlAlchemyAIUnitOfWorkFactory(runtime_engine)
        failed = _run(
            tenant_id=tenant_id,
            principal_id=principal_id,
            work_id=work_id,
            status=GenerationRunStatus.FAILED,
            key="e0",
            lease_expires_at=None,
        )
        with factory(tenant_id) as uow:
            uow.generation_runs.insert(failed)
            uow.commit()
        retry = _run(
            tenant_id=tenant_id,
            principal_id=principal_id,
            work_id=work_id,
            key="e1",
        )
        with factory(tenant_id) as uow:
            uow.generation_runs.insert(retry)
            uow.commit()

    def test_provider_and_model_do_not_bypass_fence_a(
        self, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        work_id = uuid.uuid7()
        factory = SqlAlchemyAIUnitOfWorkFactory(runtime_engine)
        first = _run(
            tenant_id=tenant_id,
            principal_id=principal_id,
            work_id=work_id,
            status=GenerationRunStatus.SUCCEEDED,
            key="f0",
            provider_id="provider.a",
            model_id="model-a",
            lease_expires_at=None,
        )
        with factory(tenant_id) as uow:
            uow.generation_runs.insert(first)
            uow.commit()
        for key, provider, model in (
            ("f1", "provider.b", "model-a"),
            ("f2", "provider.a", "model-b"),
        ):
            competing = _run(
                tenant_id=tenant_id,
                principal_id=principal_id,
                work_id=work_id,
                key=key,
                provider_id=provider,
                model_id=model,
            )
            with factory(tenant_id) as uow:
                with pytest.raises(GenerationRunConflict):
                    uow.generation_runs.insert(competing)
                    uow.commit()

    def test_tenants_independently_scoped(self, runtime_engine: Engine) -> None:
        tenant_a = uuid.uuid7()
        tenant_b = uuid.uuid7()
        principal = uuid.uuid7()
        work_id = uuid.uuid7()
        factory = SqlAlchemyAIUnitOfWorkFactory(runtime_engine)
        a = _run(
            tenant_id=tenant_a,
            principal_id=principal,
            work_id=work_id,
            status=GenerationRunStatus.SUCCEEDED,
            key="g-a",
            lease_expires_at=None,
        )
        b = _run(
            tenant_id=tenant_b,
            principal_id=principal,
            work_id=work_id,
            status=GenerationRunStatus.SUCCEEDED,
            key="g-b",
            lease_expires_at=None,
        )
        with factory(tenant_a) as uow:
            uow.generation_runs.insert(a)
            uow.commit()
        with factory(tenant_b) as uow:
            uow.generation_runs.insert(b)
            uow.commit()
        with factory(tenant_b) as uow:
            assert (
                uow.generation_runs.find_outcome_for_work_revision_capability(
                    work_resource_id=work_id,
                    work_resource_revision=0,
                    capability_id=CAP_WORKSHEET,
                )
                is not None
            )


class TestArchitectureAbuseGuards:
    def test_no_forbidden_structures_in_i02_migration(self) -> None:
        path = (
            REPO_ROOT
            / "migrations"
            / "versions"
            / "tosd040001_multi_artifact_provenance_and_generation_fences.py"
        )
        text_src = path.read_text(encoding="utf-8")
        lowered = text_src.lower()
        for needle in (
            "generation_artifacts",
            "generation_validated_outputs",
            "create table",
            "partially_ready",
            "temporalio",
            "openai",
            "preparation_kit",
        ):
            assert needle not in lowered
        assert "'PARTIAL'" not in text_src
        assert "status = 'PARTIAL'" not in text_src
        models = (
            REPO_ROOT
            / "src"
            / "aieos"
            / "platform"
            / "ai"
            / "infrastructure"
            / "persistence"
            / "models.py"
        ).read_text(encoding="utf-8")
        assert "result_content_id" in models
        assert "generation_artifacts" not in models
        assert "uq_ai_generation_runs_work_active_or_succeeded" not in models
        assert "uq_ai_generation_runs_work_revision_capability_outcome" in models
        assert "uq_ai_generation_runs_work_capability_running" in models
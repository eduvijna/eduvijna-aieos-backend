"""GCI-I14 adversarial: AI provenance and materialization atomicity."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import text

from aieos.domains.content.application.ai_materialization import (
    MaterializeAIGeneratedContentVersionService,
)
from aieos.domains.content.application.errors import (
    PersistenceOperationFailed,
)
from aieos.domains.content.application.models import (
    AIGeneratedVersionMaterializationCommand,
    VersionAssetAssociationSpec,
)
from aieos.domains.content.domain.identities import AggregateRevision, ContentId
from aieos.domains.content.domain.provenance import (
    AIGenerationProvenanceV1,
    ai_generation_provenance_as_json,
)
from aieos.domains.content.infrastructure.persistence.repositories import (
    SqlAlchemyContentVersionRepository,
    SqlAlchemyVersionAssetRefRepository,
)
from aieos.domains.content.infrastructure.persistence.uow import (
    SqlAlchemyContentUnitOfWorkFactory,
)
from aieos.platform.events.models import MutationEventContext
from aieos.platform.events.persistence.repositories import SqlAlchemyOutboxRepository
from aieos.platform.resources import ResourceRef
from tests.dbutil import REPO_ROOT
from tests.fakes import (
    AllowAIGenerationAuthorization,
    AllowAssetReferenceValidation,
    make_test_schema_registry,
)

pytestmark = pytest.mark.gci_i14

FIXED_NOW = datetime(2026, 8, 14, 23, 30, tzinfo=UTC)
AI_SRC = (
    REPO_ROOT
    / "src"
    / "aieos"
    / "domains"
    / "content"
    / "application"
    / "ai_materialization.py"
)


def _event_context(correlation_id: uuid.UUID | None = None) -> MutationEventContext:
    actor = uuid.uuid7()
    return MutationEventContext(
        correlation_id=correlation_id or uuid.uuid7(),
        causation_id=uuid.uuid7(),
        actor_principal_id=actor,
        effective_actor_id=actor,
    )


def _seed_content(bootstrap_engine, tenant_id: uuid.UUID) -> ContentId:
    content_id = ContentId.generate()
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
                "content_id": content_id.value,
                "tenant_id": tenant_id,
                "owner": owner,
                "now": FIXED_NOW,
            },
        )
    return content_id


def _provenance(correlation_id: uuid.UUID) -> AIGenerationProvenanceV1:
    return AIGenerationProvenanceV1(
        generation_run_ref=ResourceRef("generation.run", uuid.uuid7(), None),
        prompt_execution_ref=None,
        provider_id="test.provider",
        model_id="neutral-model",
        capability_id="content.generate.lesson",
        source_refs=(),
        policy_refs=(),
        evaluation_refs=(),
        correlation_id=correlation_id,
    )


def _materializer(runtime_engine, *, auth=None, assets=None):
    return MaterializeAIGeneratedContentVersionService(
        SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
        make_test_schema_registry(),
        assets or AllowAssetReferenceValidation(),
        auth or AllowAIGenerationAuthorization(),
    )


def _counts(bootstrap_engine, content_id: ContentId) -> tuple[int, int, int, int, int]:
    with bootstrap_engine.connect() as conn:
        versions = conn.execute(
            text("SELECT count(*) FROM content.content_versions WHERE content_id = :cid"),
            {"cid": content_id.value},
        ).scalar_one()
        refs = conn.execute(
            text("SELECT count(*) FROM content.version_asset_refs WHERE content_id = :cid"),
            {"cid": content_id.value},
        ).scalar_one()
        events = conn.execute(
            text(
                """
                SELECT count(*) FROM integration.outbox_messages
                WHERE aggregate_id = :cid
                  AND event_type = 'io.eduvijna.aieos.content.content.version_created.v1'
                """
            ),
            {"cid": content_id.value},
        ).scalar_one()
        reviews = conn.execute(
            text("SELECT count(*) FROM content.review_decisions WHERE content_id = :cid"),
            {"cid": content_id.value},
        ).scalar_one()
        pubs = conn.execute(
            text("SELECT count(*) FROM content.publications WHERE content_id = :cid"),
            {"cid": content_id.value},
        ).scalar_one()
    return int(versions), int(refs), int(events), int(reviews), int(pubs)


def _head(bootstrap_engine, content_id: ContentId):
    with bootstrap_engine.connect() as conn:
        return conn.execute(
            text(
                """
                SELECT current_version_id, published_version_id, aggregate_revision,
                       stewardship_state
                FROM content.contents WHERE content_id = :cid
                """
            ),
            {"cid": content_id.value},
        ).one()


class TestAIProvenanceRejection:
    def test_missing_unknown_secret_and_schema_version_coercions_rejected(self) -> None:
        from aieos.domains.content.domain.errors import InvalidAIGenerationProvenanceError
        from aieos.domains.content.domain.provenance import (
            ai_generation_provenance_from_json,
        )

        base = ai_generation_provenance_as_json(_provenance(uuid.uuid7()))
        cases = [
            {k: v for k, v in base.items() if k != "generation_run_ref"},
            {**base, "api_key": "SECRET"},
            {**base, "secret": "nope"},
            {**base, "schema_version": True},
            {**base, "schema_version": 1.0},
            {**base, "schema_version": "1"},
            {**base, "unknown_key": "x"},
        ]
        for payload in cases:
            with pytest.raises(InvalidAIGenerationProvenanceError):
                ai_generation_provenance_from_json(payload)


class TestAIMaterializeAtomicity:
    def test_version_insert_failure_no_head_advance(
        self, runtime_engine, bootstrap_engine, monkeypatch
    ) -> None:
        tenant_id = uuid.uuid7()
        content_id = _seed_content(bootstrap_engine, tenant_id)
        ctx = _event_context()

        def boom(self, *args, **kwargs):  # noqa: ANN001
            raise PersistenceOperationFailed("version insert failed")

        monkeypatch.setattr(SqlAlchemyContentVersionRepository, "insert", boom)
        with pytest.raises(PersistenceOperationFailed):
            _materializer(runtime_engine).materialize(
                tenant_id,
                uuid.uuid7(),
                AIGeneratedVersionMaterializationCommand(
                    content_id=content_id,
                    expected_aggregate_revision=AggregateRevision(0),
                    schema_id="test.generic",
                    schema_version=1,
                    payload={"marker": "ai"},
                    provenance=_provenance(ctx.correlation_id),
                    asset_refs=(),
                ),
                event_context=ctx,
                now=FIXED_NOW,
            )
        head = _head(bootstrap_engine, content_id)
        assert head.current_version_id is None
        assert int(head.aggregate_revision) == 0
        assert _counts(bootstrap_engine, content_id)[0] == 0

    def test_asset_insert_many_failure_preserves_published_pointer(
        self, runtime_engine, bootstrap_engine, monkeypatch
    ) -> None:
        tenant_id = uuid.uuid7()
        content_id = _seed_content(bootstrap_engine, tenant_id)
        published_vid = uuid.uuid7()
        with bootstrap_engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO content.content_versions (
                        version_id, tenant_id, content_id, version_number, parent_version_id,
                        schema_id, schema_version, payload, payload_sha256, origin,
                        provenance, created_at, created_by_principal_id
                    ) VALUES (
                        :vid, :tid, :cid, 1, NULL, 'test.generic', 1,
                        '{"marker":"pub"}'::jsonb, :sha, 'HUMAN', NULL, :now, :owner
                    )
                    """
                ),
                {
                    "vid": published_vid,
                    "tid": tenant_id,
                    "cid": content_id.value,
                    "sha": "a" * 64,
                    "now": FIXED_NOW,
                    "owner": uuid.uuid7(),
                },
            )
            conn.execute(
                text(
                    """
                    UPDATE content.contents
                    SET current_version_id = :vid, published_version_id = :vid,
                        aggregate_revision = 1, stewardship_state = 'APPROVED'
                    WHERE content_id = :cid
                    """
                ),
                {"vid": published_vid, "cid": content_id.value},
            )

        def boom(self, refs):  # noqa: ANN001
            raise PersistenceOperationFailed("asset insert failed")

        monkeypatch.setattr(SqlAlchemyVersionAssetRefRepository, "insert_many", boom)
        asset = VersionAssetAssociationSpec(
            role="primary",
            ordinal=0,
            required=True,
            resource_ref=ResourceRef("asset.file", uuid.uuid7(), None),
        )
        ctx = _event_context()
        with pytest.raises(PersistenceOperationFailed):
            _materializer(runtime_engine).materialize(
                tenant_id,
                uuid.uuid7(),
                AIGeneratedVersionMaterializationCommand(
                    content_id=content_id,
                    expected_aggregate_revision=AggregateRevision(1),
                    schema_id="test.generic",
                    schema_version=1,
                    payload={"marker": "ai2"},
                    provenance=_provenance(ctx.correlation_id),
                    asset_refs=(asset,),
                ),
                event_context=ctx,
                now=FIXED_NOW,
            )
        head = _head(bootstrap_engine, content_id)
        assert head.published_version_id == published_vid
        assert head.current_version_id == published_vid
        assert int(head.aggregate_revision) == 1

    def test_outbox_failure_rolls_back_materialize(
        self, runtime_engine, bootstrap_engine, monkeypatch
    ) -> None:
        tenant_id = uuid.uuid7()
        content_id = _seed_content(bootstrap_engine, tenant_id)
        ctx = _event_context()

        def boom(self, *args, **kwargs):  # noqa: ANN001
            raise PersistenceOperationFailed("outbox insert failed")

        monkeypatch.setattr(SqlAlchemyOutboxRepository, "insert", boom)
        with pytest.raises(PersistenceOperationFailed):
            _materializer(runtime_engine).materialize(
                tenant_id,
                uuid.uuid7(),
                AIGeneratedVersionMaterializationCommand(
                    content_id=content_id,
                    expected_aggregate_revision=AggregateRevision(0),
                    schema_id="test.generic",
                    schema_version=1,
                    payload={"marker": "ai"},
                    provenance=_provenance(ctx.correlation_id),
                    asset_refs=(),
                ),
                event_context=ctx,
                now=FIXED_NOW,
            )
        head = _head(bootstrap_engine, content_id)
        assert head.current_version_id is None
        assert int(head.aggregate_revision) == 0

    def test_successful_materialize_generated_zero_reviews_pubs(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        content_id = _seed_content(bootstrap_engine, tenant_id)
        ctx = _event_context()
        result = _materializer(runtime_engine).materialize(
            tenant_id,
            uuid.uuid7(),
            AIGeneratedVersionMaterializationCommand(
                content_id=content_id,
                expected_aggregate_revision=AggregateRevision(0),
                schema_id="test.generic",
                schema_version=1,
                payload={"marker": "ai"},
                provenance=_provenance(ctx.correlation_id),
                asset_refs=(),
            ),
            event_context=ctx,
            now=FIXED_NOW,
        )
        head = _head(bootstrap_engine, content_id)
        assert head.stewardship_state == "GENERATED"
        assert head.current_version_id == result.version_id.value
        versions, refs, events, reviews, pubs = _counts(bootstrap_engine, content_id)
        assert versions == 1
        assert reviews == 0
        assert pubs == 0
        assert events == 1
        assert refs == 0

    def test_ai_materialization_source_has_no_approve_publish(self) -> None:
        lowered = AI_SRC.read_text(encoding="utf-8").lower()
        assert "approve" not in lowered
        assert "publish" not in lowered

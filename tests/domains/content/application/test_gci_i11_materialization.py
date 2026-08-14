"""GCI-I11 AI materialization service and append provenance hardening."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.domains.content.application.ai_materialization import (
    MaterializeAIGeneratedContentVersionService,
)
from aieos.domains.content.application.errors import (
    AIGenerationForbidden,
    AIProvenanceInvalid,
    AssetReferenceValidationFailed,
    ContentNotFound,
    ContentPayloadInvalid,
    PersistenceOperationFailed,
)
from aieos.domains.content.application.models import (
    AIGeneratedVersionMaterializationCommand,
    AppendContentVersionCommand,
    VersionAssetAssociationSpec,
)
from aieos.domains.content.application.services import AppendContentVersionService
from aieos.domains.content.domain.identities import (
    AggregateRevision,
    ContentId,
    ContentVersionId,
    VersionNumber,
)
from aieos.domains.content.domain.origin import ContentOrigin
from aieos.domains.content.domain.provenance import (
    AIGenerationProvenanceV1,
    ai_generation_provenance_as_json,
    ai_generation_provenance_from_json,
)
from aieos.domains.content.domain.schema import SchemaId, SchemaVersion
from aieos.domains.content.domain.version import ContentPayload, ContentVersion
from aieos.domains.content.infrastructure.persistence.repositories import (
    SqlAlchemyVersionAssetRefRepository,
)
from aieos.domains.content.infrastructure.persistence.uow import (
    SqlAlchemyContentUnitOfWorkFactory,
)
from aieos.platform.events.models import MutationEventContext
from aieos.platform.resources import ResourceRef
from tests.fakes import (
    AllowAIGenerationAuthorization,
    AllowAssetReferenceValidation,
    make_test_schema_registry,
)

pytestmark = pytest.mark.gci_i11

FIXED_NOW = datetime(2026, 8, 14, 20, 0, tzinfo=UTC)


def _event_context(correlation_id: uuid.UUID | None = None) -> MutationEventContext:
    actor = uuid.uuid7()
    return MutationEventContext(
        correlation_id=correlation_id or uuid.uuid7(),
        causation_id=uuid.uuid7(),
        actor_principal_id=actor,
        effective_actor_id=actor,
    )


def _seed_content(bootstrap_engine: Engine, tenant_id: uuid.UUID) -> ContentId:
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


def _materializer(
    runtime_engine: Engine,
    *,
    auth=None,
    assets=None,
) -> MaterializeAIGeneratedContentVersionService:
    return MaterializeAIGeneratedContentVersionService(
        SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
        make_test_schema_registry(),
        assets or AllowAssetReferenceValidation(),
        auth or AllowAIGenerationAuthorization(),
    )


def _counts(bootstrap_engine: Engine, content_id: ContentId) -> tuple[int, int, int]:
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
    return int(versions), int(refs), int(events)


def _head(bootstrap_engine: Engine, content_id: ContentId):
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


class TestDirectAppendAIProvenance:
    def test_untyped_mapping_rejected(self, runtime_engine, bootstrap_engine) -> None:
        tenant_id = uuid.uuid7()
        content_id = _seed_content(bootstrap_engine, tenant_id)
        version = ContentVersion(
            version_id=ContentVersionId.generate(),
            tenant_id=tenant_id,
            content_id=content_id,
            version_number=VersionNumber(1),
            parent_version_id=None,
            schema_id=SchemaId("test.generic"),
            schema_version=SchemaVersion(1),
            payload=ContentPayload.from_mapping({"marker": "v1"}),
            origin=ContentOrigin.AI,
            created_at=FIXED_NOW,
            created_by_principal_id=uuid.uuid7(),
        )
        service = AppendContentVersionService(
            SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
            AllowAssetReferenceValidation(),
        )
        with pytest.raises(AIProvenanceInvalid):
            service.append(
                tenant_id,
                AppendContentVersionCommand(
                    expected_aggregate_revision=AggregateRevision(0),
                    version=version,
                    provenance={"anything": "goes"},
                ),
                event_context=_event_context(),
                now=FIXED_NOW,
            )
        assert _counts(bootstrap_engine, content_id) == (0, 0, 0)

    def test_canonical_provenance_succeeds(self, runtime_engine, bootstrap_engine) -> None:
        tenant_id = uuid.uuid7()
        content_id = _seed_content(bootstrap_engine, tenant_id)
        correlation_id = uuid.uuid7()
        version = ContentVersion(
            version_id=ContentVersionId.generate(),
            tenant_id=tenant_id,
            content_id=content_id,
            version_number=VersionNumber(1),
            parent_version_id=None,
            schema_id=SchemaId("test.generic"),
            schema_version=SchemaVersion(1),
            payload=ContentPayload.from_mapping({"marker": "v1"}),
            origin=ContentOrigin.AI,
            created_at=FIXED_NOW,
            created_by_principal_id=uuid.uuid7(),
        )
        provenance = _provenance(correlation_id)
        service = AppendContentVersionService(
            SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
            AllowAssetReferenceValidation(),
        )
        service.append(
            tenant_id,
            AppendContentVersionCommand(
                expected_aggregate_revision=AggregateRevision(0),
                version=version,
                provenance=provenance,
            ),
            event_context=_event_context(correlation_id),
            now=FIXED_NOW,
        )
        with SqlAlchemyContentUnitOfWorkFactory(runtime_engine)(tenant_id) as uow:
            stored = uow.versions.get_provenance(version.version_id)
            uow.commit()
        assert ai_generation_provenance_from_json(stored) == provenance


class TestMaterializeAIGeneratedContentVersion:
    def test_happy_path_generated_state_no_auto_publish(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id = _seed_content(bootstrap_engine, tenant_id)
        correlation_id = uuid.uuid7()
        result = _materializer(runtime_engine).materialize(
            tenant_id,
            principal_id,
            AIGeneratedVersionMaterializationCommand(
                content_id=content_id,
                expected_aggregate_revision=AggregateRevision(0),
                schema_id="test.generic",
                schema_version=1,
                payload={"marker": "ai-v1"},
                provenance=_provenance(correlation_id),
            ),
            event_context=_event_context(correlation_id),
            now=FIXED_NOW,
        )
        head = _head(bootstrap_engine, content_id)
        assert head.current_version_id == result.version_id.value
        assert head.published_version_id is None
        assert head.stewardship_state == "GENERATED"
        assert int(head.aggregate_revision) == 1
        with bootstrap_engine.connect() as conn:
            pubs = conn.execute(
                text("SELECT count(*) FROM content.publications WHERE content_id = :cid"),
                {"cid": content_id.value},
            ).scalar_one()
            reviews = conn.execute(
                text(
                    "SELECT count(*) FROM content.review_decisions WHERE content_id = :cid"
                ),
                {"cid": content_id.value},
            ).scalar_one()
            origin = conn.execute(
                text(
                    "SELECT origin FROM content.content_versions WHERE version_id = :vid"
                ),
                {"vid": result.version_id.value},
            ).scalar_one()
            event = conn.execute(
                text(
                    """
                    SELECT envelope FROM integration.outbox_messages
                    WHERE aggregate_id = :cid
                      AND event_type = 'io.eduvijna.aieos.content.content.version_created.v1'
                    """
                ),
                {"cid": content_id.value},
            ).scalar_one()
        assert int(pubs) == 0
        assert int(reviews) == 0
        assert origin == "AI"
        data = event["data"]
        assert data["origin"] == "AI"
        assert "provenance" not in data
        assert "provider" not in data
        assert "model" not in str(data).lower()

    def test_authorization_denial(self, runtime_engine, bootstrap_engine) -> None:
        tenant_id = uuid.uuid7()
        content_id = _seed_content(bootstrap_engine, tenant_id)
        correlation_id = uuid.uuid7()
        with pytest.raises(AIGenerationForbidden):
            _materializer(
                runtime_engine, auth=AllowAIGenerationAuthorization(allow=False)
            ).materialize(
                tenant_id,
                uuid.uuid7(),
                AIGeneratedVersionMaterializationCommand(
                    content_id=content_id,
                    expected_aggregate_revision=AggregateRevision(0),
                    schema_id="test.generic",
                    schema_version=1,
                    payload={"marker": "ai-v1"},
                    provenance=_provenance(correlation_id),
                ),
                event_context=_event_context(correlation_id),
                now=FIXED_NOW,
            )
        assert _counts(bootstrap_engine, content_id) == (0, 0, 0)
        head = _head(bootstrap_engine, content_id)
        assert head.current_version_id is None
        assert int(head.aggregate_revision) == 0

    def test_cross_tenant_nondisclosure(self, runtime_engine, bootstrap_engine) -> None:
        tenant_a = uuid.uuid7()
        tenant_b = uuid.uuid7()
        content_id = _seed_content(bootstrap_engine, tenant_b)
        correlation_id = uuid.uuid7()
        with pytest.raises(ContentNotFound):
            _materializer(runtime_engine).materialize(
                tenant_a,
                uuid.uuid7(),
                AIGeneratedVersionMaterializationCommand(
                    content_id=content_id,
                    expected_aggregate_revision=AggregateRevision(0),
                    schema_id="test.generic",
                    schema_version=1,
                    payload={"marker": "ai-v1"},
                    provenance=_provenance(correlation_id),
                ),
                event_context=_event_context(correlation_id),
                now=FIXED_NOW,
            )
        assert _counts(bootstrap_engine, content_id) == (0, 0, 0)

    def test_invalid_payload(self, runtime_engine, bootstrap_engine) -> None:
        tenant_id = uuid.uuid7()
        content_id = _seed_content(bootstrap_engine, tenant_id)
        correlation_id = uuid.uuid7()
        with pytest.raises(ContentPayloadInvalid):
            _materializer(runtime_engine).materialize(
                tenant_id,
                uuid.uuid7(),
                AIGeneratedVersionMaterializationCommand(
                    content_id=content_id,
                    expected_aggregate_revision=AggregateRevision(0),
                    schema_id="test.generic",
                    schema_version=1,
                    payload={"wrong": True},
                    provenance=_provenance(correlation_id),
                ),
                event_context=_event_context(correlation_id),
                now=FIXED_NOW,
            )
        assert _counts(bootstrap_engine, content_id) == (0, 0, 0)

    def test_invalid_asset_binding(self, runtime_engine, bootstrap_engine) -> None:
        tenant_id = uuid.uuid7()
        content_id = _seed_content(bootstrap_engine, tenant_id)
        correlation_id = uuid.uuid7()
        denied = uuid.uuid7()
        with pytest.raises(AssetReferenceValidationFailed):
            _materializer(
                runtime_engine,
                assets=AllowAssetReferenceValidation(deny_ids={denied}),
            ).materialize(
                tenant_id,
                uuid.uuid7(),
                AIGeneratedVersionMaterializationCommand(
                    content_id=content_id,
                    expected_aggregate_revision=AggregateRevision(0),
                    schema_id="test.generic",
                    schema_version=1,
                    payload={"marker": "ai-v1"},
                    provenance=_provenance(correlation_id),
                    asset_refs=(
                        VersionAssetAssociationSpec(
                            resource_ref=ResourceRef("asset.image", denied, None),
                            role="primary",
                            ordinal=0,
                            required=True,
                        ),
                    ),
                ),
                event_context=_event_context(correlation_id),
                now=FIXED_NOW,
            )
        assert _counts(bootstrap_engine, content_id) == (0, 0, 0)

    def test_correlation_mismatch(self, runtime_engine, bootstrap_engine) -> None:
        tenant_id = uuid.uuid7()
        content_id = _seed_content(bootstrap_engine, tenant_id)
        with pytest.raises(AIProvenanceInvalid):
            _materializer(runtime_engine).materialize(
                tenant_id,
                uuid.uuid7(),
                AIGeneratedVersionMaterializationCommand(
                    content_id=content_id,
                    expected_aggregate_revision=AggregateRevision(0),
                    schema_id="test.generic",
                    schema_version=1,
                    payload={"marker": "ai-v1"},
                    provenance=_provenance(uuid.uuid7()),
                ),
                event_context=_event_context(uuid.uuid7()),
                now=FIXED_NOW,
            )
        assert _counts(bootstrap_engine, content_id) == (0, 0, 0)

    def test_insert_many_failure_rolls_back_with_published_pointer(
        self, runtime_engine, bootstrap_engine, monkeypatch
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id = _seed_content(bootstrap_engine, tenant_id)
        correlation_id = uuid.uuid7()
        first = _materializer(runtime_engine).materialize(
            tenant_id,
            principal_id,
            AIGeneratedVersionMaterializationCommand(
                content_id=content_id,
                expected_aggregate_revision=AggregateRevision(0),
                schema_id="test.generic",
                schema_version=1,
                payload={"marker": "ai-v1"},
                provenance=_provenance(correlation_id),
            ),
            event_context=_event_context(correlation_id),
            now=FIXED_NOW,
        )
        with bootstrap_engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE content.contents SET published_version_id = :vid, "
                    "stewardship_state = 'APPROVED' WHERE content_id = :cid"
                ),
                {"vid": first.version_id.value, "cid": content_id.value},
            )
        before = _head(bootstrap_engine, content_id)

        def boom(self, refs):
            raise PersistenceOperationFailed("injected VersionAssetRef failure")

        monkeypatch.setattr(SqlAlchemyVersionAssetRefRepository, "insert_many", boom)
        correlation2 = uuid.uuid7()
        with pytest.raises(PersistenceOperationFailed):
            _materializer(runtime_engine).materialize(
                tenant_id,
                principal_id,
                AIGeneratedVersionMaterializationCommand(
                    content_id=content_id,
                    expected_aggregate_revision=AggregateRevision(
                        int(before.aggregate_revision)
                    ),
                    schema_id="test.generic",
                    schema_version=1,
                    payload={"marker": "ai-v2"},
                    provenance=_provenance(correlation2),
                    asset_refs=(
                        VersionAssetAssociationSpec(
                            resource_ref=ResourceRef("asset.image", uuid.uuid7(), None),
                            role="primary",
                            ordinal=0,
                            required=True,
                        ),
                    ),
                ),
                event_context=_event_context(correlation2),
                now=FIXED_NOW,
            )
        after = _head(bootstrap_engine, content_id)
        assert after.current_version_id == before.current_version_id
        assert after.published_version_id == first.version_id.value
        assert int(after.aggregate_revision) == int(before.aggregate_revision)
        assert _counts(bootstrap_engine, content_id)[0] == 1
        assert _counts(bootstrap_engine, content_id)[1] == 0

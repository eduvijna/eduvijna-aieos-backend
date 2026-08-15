"""GCI-I13 ImportMigratedContentService behavior, GCI-G12, and atomicity."""

from __future__ import annotations

from aieos.domains.content.application.audit import api_mutation_audit_provenance

import threading
import uuid
from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.domains.content.application.catalog import StaticContentTypeCatalog
from aieos.domains.content.application.errors import (
    AssetReferenceValidationFailed,
    ContentPayloadInvalid,
    MigrationForbidden,
    MigrationSourceConflict,
    PersistenceOperationFailed,
)
from aieos.domains.content.application.migration_import import ImportMigratedContentService
from aieos.domains.content.application.migration_models import MigrationContentCandidate
from aieos.domains.content.application.models import VersionAssetAssociationSpec
from aieos.domains.content.application.publish import PublishContentService
from aieos.domains.content.application.review import ReviewCommandService
from aieos.domains.content.application.services import AppendContentVersionService
from aieos.domains.content.domain.identities import (
    AggregateRevision,
    ContentId,
    ContentVersionId,
    VersionNumber,
)
from aieos.domains.content.domain.migration import MigrationSourceIdentity
from aieos.domains.content.domain.origin import ContentOrigin
from aieos.domains.content.domain.schema import SchemaId, SchemaVersion
from aieos.domains.content.domain.version import ContentPayload, ContentVersion
from aieos.domains.content.infrastructure.persistence.repositories import (
    SqlAlchemyContentRepository,
    SqlAlchemyContentVersionRepository,
    SqlAlchemyMigrationImportRecordRepository,
    SqlAlchemyVersionAssetRefRepository,
)
from aieos.domains.content.infrastructure.persistence.uow import (
    SqlAlchemyContentUnitOfWorkFactory,
)
from aieos.domains.content.infrastructure.persistence.source_serialization import (
    SqlAlchemyMigrationSourceSerializationGate,
)
from aieos.platform.events.models import MutationEventContext
from aieos.platform.events.persistence.repositories import SqlAlchemyOutboxRepository
from aieos.platform.resources import ResourceRef
from tests.fakes import (
    AllowAssetCurrentGovernance,
    AllowAssetReferenceValidation,
    AllowMigrationAuthorization,
    AllowPublicationAuthorization,
    AllowPublicationGovernance,
    AllowReviewAuthorization,
    AllowReviewCommentPolicy,
    make_test_schema_registry,
)

pytestmark = pytest.mark.gci_i13

FIXED_NOW = datetime(2026, 8, 14, 22, 0, tzinfo=UTC)
DIGEST_A = "a" * 64
DIGEST_B = "b" * 64


def _event_context(actor: UUID | None = None) -> MutationEventContext:
    principal = actor or uuid.uuid7()
    return MutationEventContext(
        correlation_id=uuid.uuid7(),
        causation_id=uuid.uuid7(),
        actor_principal_id=principal,
        effective_actor_id=principal,
    )


def _candidate(
    *,
    source_resource_id: str = "src-1",
    digest: str = DIGEST_A,
    source_version: str | None = "1",
    mapping_version: int = 1,
    mapping_id: str = "edu.lesson.v1",
    asset_refs: tuple[VersionAssetAssociationSpec, ...] = (),
    payload: dict | None = None,
) -> MigrationContentCandidate:
    return MigrationContentCandidate(
        source_identity=MigrationSourceIdentity(
            "legacy.edu", "lesson", source_resource_id
        ),
        source_version=source_version,
        source_digest_sha256=digest,
        migration_batch_id=uuid.uuid7(),
        mapping_id=mapping_id,
        mapping_version=mapping_version,
        target_owner_principal_id=uuid.uuid7(),
        content_type="test.generic",
        title="Imported title",
        description="Imported description",
        locale="en-IN",
        schema_id="test.generic",
        schema_version=1,
        payload=payload if payload is not None else {"marker": "import-v1"},
        asset_refs=asset_refs,
    )


def _importer(
    engine: Engine,
    *,
    auth=None,
    assets=None,
    after_target_failure=None,
) -> ImportMigratedContentService:
    return ImportMigratedContentService(
        SqlAlchemyContentUnitOfWorkFactory(engine),
        StaticContentTypeCatalog({"test.generic"}),
        make_test_schema_registry(),
        assets or AllowAssetReferenceValidation(),
        auth or AllowMigrationAuthorization(),
        SqlAlchemyMigrationSourceSerializationGate(engine),
        after_target_failure=after_target_failure,
    )


def _counts(
    bootstrap_engine: Engine,
    *,
    tenant_id: UUID | None = None,
    content_id: UUID | None = None,
) -> dict[str, int]:
    with bootstrap_engine.connect() as conn:
        if tenant_id is None:
            contents = conn.execute(text("SELECT count(*) FROM content.contents")).scalar_one()
            versions = conn.execute(
                text("SELECT count(*) FROM content.content_versions")
            ).scalar_one()
            mig = conn.execute(
                text("SELECT count(*) FROM content.migration_import_records")
            ).scalar_one()
            created = conn.execute(
                text(
                    """
                    SELECT count(*) FROM integration.outbox_messages
                    WHERE event_type = 'io.eduvijna.aieos.content.content.created.v1'
                    """
                )
            ).scalar_one()
            versioned = conn.execute(
                text(
                    """
                    SELECT count(*) FROM integration.outbox_messages
                    WHERE event_type =
                      'io.eduvijna.aieos.content.content.version_created.v1'
                    """
                )
            ).scalar_one()
            reviews = conn.execute(
                text("SELECT count(*) FROM content.review_decisions")
            ).scalar_one()
            pubs = conn.execute(
                text("SELECT count(*) FROM content.publications")
            ).scalar_one()
        else:
            contents = conn.execute(
                text("SELECT count(*) FROM content.contents WHERE tenant_id = :tid"),
                {"tid": tenant_id},
            ).scalar_one()
            versions = conn.execute(
                text(
                    "SELECT count(*) FROM content.content_versions WHERE tenant_id = :tid"
                ),
                {"tid": tenant_id},
            ).scalar_one()
            mig = conn.execute(
                text(
                    "SELECT count(*) FROM content.migration_import_records "
                    "WHERE tenant_id = :tid"
                ),
                {"tid": tenant_id},
            ).scalar_one()
            created = conn.execute(
                text(
                    """
                    SELECT count(*) FROM integration.outbox_messages
                    WHERE tenant_id = :tid
                      AND event_type = 'io.eduvijna.aieos.content.content.created.v1'
                    """
                ),
                {"tid": tenant_id},
            ).scalar_one()
            versioned = conn.execute(
                text(
                    """
                    SELECT count(*) FROM integration.outbox_messages
                    WHERE tenant_id = :tid
                      AND event_type =
                        'io.eduvijna.aieos.content.content.version_created.v1'
                    """
                ),
                {"tid": tenant_id},
            ).scalar_one()
            reviews = conn.execute(
                text(
                    "SELECT count(*) FROM content.review_decisions WHERE tenant_id = :tid"
                ),
                {"tid": tenant_id},
            ).scalar_one()
            pubs = conn.execute(
                text("SELECT count(*) FROM content.publications WHERE tenant_id = :tid"),
                {"tid": tenant_id},
            ).scalar_one()
        refs = 0
        if content_id is not None:
            refs = conn.execute(
                text(
                    "SELECT count(*) FROM content.version_asset_refs WHERE content_id = :cid"
                ),
                {"cid": content_id},
            ).scalar_one()
    return {
        "contents": int(contents),
        "versions": int(versions),
        "migration": int(mig),
        "created": int(created),
        "versioned": int(versioned),
        "reviews": int(reviews),
        "pubs": int(pubs),
        "refs": int(refs),
    }


def _head(bootstrap_engine: Engine, content_id: UUID):
    with bootstrap_engine.connect() as conn:
        return conn.execute(
            text(
                """
                SELECT stewardship_state, aggregate_revision, published_version_id,
                       current_version_id
                FROM content.contents WHERE content_id = :cid
                """
            ),
            {"cid": content_id},
        ).one()


def _mig_row(bootstrap_engine: Engine, tenant_id: UUID, source_resource_id: str = "src-1"):
    with bootstrap_engine.connect() as conn:
        return conn.execute(
            text(
                """
                SELECT outcome, attempt_count, target_content_id, target_version_id,
                       source_digest_sha256, failure_code
                FROM content.migration_import_records
                WHERE tenant_id = :tid AND source_resource_id = :sid
                """
            ),
            {"tid": tenant_id, "sid": source_resource_id},
        ).one_or_none()


class TestDirectAppendImportProvenance:
    def test_import_requires_typed_provenance(
        self, migration_runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        owner = uuid.uuid7()
        content_id = ContentId.generate()
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
                        :cid, :tid, :owner, 'test.generic', 'Title',
                        'Description', 'en-IN', 'DRAFT', NULL,
                        NULL, 0, :now, :owner, :now, NULL
                    )
                    """
                ),
                {
                    "cid": content_id.value,
                    "tid": tenant_id,
                    "owner": owner,
                    "now": FIXED_NOW,
                },
            )
        version = ContentVersion(
            version_id=ContentVersionId.generate(),
            tenant_id=tenant_id,
            content_id=content_id,
            version_number=VersionNumber(1),
            parent_version_id=None,
            schema_id=SchemaId("test.generic"),
            schema_version=SchemaVersion(1),
            payload=ContentPayload.from_mapping({"marker": "v1"}),
            origin=ContentOrigin.IMPORT,
            created_at=FIXED_NOW,
            created_by_principal_id=owner,
        )
        from aieos.domains.content.application.errors import MigrationImportProvenanceInvalid
        from aieos.domains.content.application.models import AppendContentVersionCommand

        service = AppendContentVersionService(
            SqlAlchemyContentUnitOfWorkFactory(migration_runtime_engine),
            AllowAssetReferenceValidation(),
        )
        with pytest.raises(MigrationImportProvenanceInvalid):
            service.append(
                tenant_id,
                AppendContentVersionCommand(
                    expected_aggregate_revision=AggregateRevision(0),
                    version=version,
                    provenance={"kind": "migration_import"},
                ),
                event_context=_event_context(),
                now=FIXED_NOW,
            )


class TestImportHappyPath:
    def test_new_source_imports_once(
        self, migration_runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        result = _importer(migration_runtime_engine).import_content(
            tenant_id,
            principal_id,
            _candidate(),
            event_context=_event_context(principal_id),
            now=FIXED_NOW,
        )
        assert result.replayed is False
        assert result.content_id.value.version == 7
        assert result.version_id.value.version == 7
        head = _head(bootstrap_engine, result.content_id.value)
        assert head.stewardship_state == "GENERATED"
        assert int(head.aggregate_revision) == 1
        assert head.published_version_id is None
        counts = _counts(bootstrap_engine, tenant_id=tenant_id, content_id=result.content_id.value)
        assert counts["contents"] == 1
        assert counts["versions"] == 1
        assert counts["migration"] == 1
        assert counts["created"] == 1
        assert counts["versioned"] == 1
        assert counts["reviews"] == 0
        assert counts["pubs"] == 0
        with bootstrap_engine.connect() as conn:
            origin, provenance = conn.execute(
                text(
                    """
                    SELECT origin, provenance FROM content.content_versions
                    WHERE version_id = :vid
                    """
                ),
                {"vid": result.version_id.value},
            ).one()
            events = conn.execute(
                text(
                    """
                    SELECT event_type, aggregate_revision, envelope
                    FROM integration.outbox_messages
                    WHERE aggregate_id = :cid
                    ORDER BY created_at, event_type
                    """
                ),
                {"cid": result.content_id.value},
            ).all()
        assert origin == "IMPORT"
        assert provenance["kind"] == "migration_import"
        assert provenance["source_digest_sha256"] == DIGEST_A
        assert [row.event_type for row in events] == [
            "io.eduvijna.aieos.content.content.created.v1",
            "io.eduvijna.aieos.content.content.version_created.v1",
        ]
        assert int(events[0].aggregate_revision) == 0
        assert int(events[1].aggregate_revision) == 1
        assert events[1].envelope["data"]["origin"] == "IMPORT"
        assert "source_digest" not in events[1].envelope["data"]

    def test_legacy_id_collision_does_not_reuse_target(
        self, migration_runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        existing = ContentId.generate()
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
                        :cid, :tid, :owner, 'test.generic', 'Existing',
                        'x', 'en-IN', 'DRAFT', NULL, NULL, 0, :now, :owner, :now, NULL
                    )
                    """
                ),
                {
                    "cid": existing.value,
                    "tid": tenant_id,
                    "owner": uuid.uuid7(),
                    "now": FIXED_NOW,
                },
            )
        result = _importer(migration_runtime_engine).import_content(
            tenant_id,
            uuid.uuid7(),
            _candidate(source_resource_id=str(existing.value)),
            event_context=_event_context(),
            now=FIXED_NOW,
        )
        assert result.content_id.value != existing.value

    def test_asset_binding_and_duplicate_slot(
        self, migration_runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        asset_id = uuid.uuid7()
        ok = _importer(migration_runtime_engine).import_content(
            tenant_id,
            uuid.uuid7(),
            _candidate(
                source_resource_id="with-asset",
                asset_refs=(
                    VersionAssetAssociationSpec(
                        resource_ref=ResourceRef("asset.image", asset_id, None),
                        role="primary",
                        ordinal=0,
                        required=True,
                    ),
                ),
            ),
            event_context=_event_context(),
            now=FIXED_NOW,
        )
        assert _counts(bootstrap_engine, tenant_id=tenant_id, content_id=ok.content_id.value)["refs"] == 1
        with pytest.raises(AssetReferenceValidationFailed):
            _importer(
                migration_runtime_engine,
                assets=AllowAssetReferenceValidation(deny_ids={asset_id}),
            ).import_content(
                tenant_id,
                uuid.uuid7(),
                _candidate(
                    source_resource_id="denied-asset",
                    asset_refs=(
                        VersionAssetAssociationSpec(
                            resource_ref=ResourceRef("asset.image", asset_id, None),
                            role="primary",
                            ordinal=0,
                            required=True,
                        ),
                    ),
                ),
                event_context=_event_context(),
                now=FIXED_NOW,
            )
        with pytest.raises(AssetReferenceValidationFailed):
            _importer(migration_runtime_engine).import_content(
                tenant_id,
                uuid.uuid7(),
                _candidate(
                    source_resource_id="dup-slot",
                    asset_refs=(
                        VersionAssetAssociationSpec(
                            resource_ref=ResourceRef("asset.image", uuid.uuid7(), None),
                            role="primary",
                            ordinal=0,
                            required=True,
                        ),
                        VersionAssetAssociationSpec(
                            resource_ref=ResourceRef("asset.image", uuid.uuid7(), None),
                            role="primary",
                            ordinal=0,
                            required=False,
                        ),
                    ),
                ),
                event_context=_event_context(),
                now=FIXED_NOW,
            )


class TestAuthAndValidation:
    def test_authorization_denial_no_target(
        self, migration_runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        with pytest.raises(MigrationForbidden):
            _importer(
                migration_runtime_engine, auth=AllowMigrationAuthorization(allow=False)
            ).import_content(
                tenant_id,
                uuid.uuid7(),
                _candidate(),
                event_context=_event_context(),
                now=FIXED_NOW,
            )
        assert _counts(bootstrap_engine, tenant_id=tenant_id)["contents"] == 0
        assert _counts(bootstrap_engine, tenant_id=tenant_id)["migration"] == 0

    def test_schema_validation_records_failed(
        self, migration_runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        with pytest.raises(ContentPayloadInvalid):
            _importer(migration_runtime_engine).import_content(
                tenant_id,
                uuid.uuid7(),
                _candidate(payload={"wrong": True}),
                event_context=_event_context(),
                now=FIXED_NOW,
            )
        row = _mig_row(bootstrap_engine, tenant_id)
        assert row.outcome == "FAILED"
        assert row.failure_code == "schema_validation_failed"
        assert row.target_content_id is None
        assert _counts(bootstrap_engine, tenant_id=tenant_id)["contents"] == 0


class TestReplayAndConflicts:
    def test_same_source_replay_and_auth(
        self, migration_runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        auth = AllowMigrationAuthorization()
        service = _importer(migration_runtime_engine, auth=auth)
        candidate = _candidate()
        first = service.import_content(
            tenant_id,
            uuid.uuid7(),
            candidate,
            event_context=_event_context(),
            now=FIXED_NOW,
        )
        before = _counts(bootstrap_engine, tenant_id=tenant_id)
        assets = AllowAssetReferenceValidation()
        auth2 = AllowMigrationAuthorization()
        second = _importer(
            migration_runtime_engine, auth=auth2, assets=assets
        ).import_content(
            tenant_id,
            uuid.uuid7(),
            candidate,
            event_context=_event_context(),
            now=FIXED_NOW,
        )
        assert second.replayed is True
        assert second.content_id == first.content_id
        assert second.version_id == first.version_id
        after = _counts(bootstrap_engine, tenant_id=tenant_id)
        assert after == before
        assert len(auth2.calls) == 1
        assert assets.calls == []
        row = _mig_row(bootstrap_engine, tenant_id)
        assert int(row.attempt_count) == 1

    def test_digest_version_mapping_conflicts(
        self, migration_runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        service = _importer(migration_runtime_engine)
        service.import_content(
            tenant_id,
            uuid.uuid7(),
            _candidate(),
            event_context=_event_context(),
            now=FIXED_NOW,
        )
        with pytest.raises(MigrationSourceConflict):
            service.import_content(
                tenant_id,
                uuid.uuid7(),
                _candidate(digest=DIGEST_B),
                event_context=_event_context(),
                now=FIXED_NOW,
            )
        with pytest.raises(MigrationSourceConflict):
            service.import_content(
                tenant_id,
                uuid.uuid7(),
                _candidate(source_version="2"),
                event_context=_event_context(),
                now=FIXED_NOW,
            )
        with pytest.raises(MigrationSourceConflict):
            service.import_content(
                tenant_id,
                uuid.uuid7(),
                _candidate(mapping_version=2),
                event_context=_event_context(),
                now=FIXED_NOW,
            )
        assert _counts(bootstrap_engine, tenant_id=tenant_id)["contents"] == 1

    def test_failed_then_retry_and_changed_digest_conflict(
        self, migration_runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        service = _importer(migration_runtime_engine)
        with pytest.raises(ContentPayloadInvalid):
            service.import_content(
                tenant_id,
                uuid.uuid7(),
                _candidate(payload={"wrong": True}),
                event_context=_event_context(),
                now=FIXED_NOW,
            )
        row = _mig_row(bootstrap_engine, tenant_id)
        assert row.outcome == "FAILED"
        assert int(row.attempt_count) == 1
        with pytest.raises(MigrationSourceConflict):
            service.import_content(
                tenant_id,
                uuid.uuid7(),
                _candidate(digest=DIGEST_B),
                event_context=_event_context(),
                now=FIXED_NOW,
            )
        result = service.import_content(
            tenant_id,
            uuid.uuid7(),
            _candidate(),
            event_context=_event_context(),
            now=FIXED_NOW,
        )
        assert result.replayed is False
        row = _mig_row(bootstrap_engine, tenant_id)
        assert row.outcome == "IMPORTED"
        assert int(row.attempt_count) == 2
        assert row.target_content_id == result.content_id.value


class TestResumeAndConcurrency:
    def test_partial_batch_resume(
        self, migration_runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        service = _importer(migration_runtime_engine)
        ids = []
        for i in range(3):
            result = service.import_content(
                tenant_id,
                uuid.uuid7(),
                _candidate(source_resource_id=f"batch-{i}"),
                event_context=_event_context(),
                now=FIXED_NOW,
            )
            ids.append(result.content_id.value)
        with pytest.raises(ContentPayloadInvalid):
            service.import_content(
                tenant_id,
                uuid.uuid7(),
                _candidate(source_resource_id="batch-fail", payload={"wrong": True}),
                event_context=_event_context(),
                now=FIXED_NOW,
            )
        assert _counts(bootstrap_engine, tenant_id=tenant_id)["contents"] == 3
        for i, content_id in enumerate(ids):
            replay = service.import_content(
                tenant_id,
                uuid.uuid7(),
                _candidate(source_resource_id=f"batch-{i}"),
                event_context=_event_context(),
                now=FIXED_NOW,
            )
            assert replay.replayed is True
            assert replay.content_id.value == content_id
        recovered = service.import_content(
            tenant_id,
            uuid.uuid7(),
            _candidate(source_resource_id="batch-fail"),
            event_context=_event_context(),
            now=FIXED_NOW,
        )
        assert recovered.replayed is False
        assert _counts(bootstrap_engine, tenant_id=tenant_id)["contents"] == 4

    def test_concurrent_same_source_one_target(
        self, migration_runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        candidate = _candidate(source_resource_id="concurrent-same")
        results: list = []
        errors: list = []

        def worker() -> None:
            try:
                results.append(
                    _importer(migration_runtime_engine).import_content(
                        tenant_id,
                        uuid.uuid7(),
                        candidate,
                        event_context=_event_context(),
                        now=FIXED_NOW,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert errors == []
        assert len(results) == 2
        assert results[0].content_id == results[1].content_id
        assert results[0].version_id == results[1].version_id
        assert _counts(bootstrap_engine, tenant_id=tenant_id)["contents"] == 1
        assert _counts(bootstrap_engine, tenant_id=tenant_id)["versions"] == 1
        assert _counts(bootstrap_engine, tenant_id=tenant_id)["created"] == 1
        assert _counts(bootstrap_engine, tenant_id=tenant_id)["versioned"] == 1

    def test_concurrent_changed_digest(
        self, migration_runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        barrier = threading.Barrier(2)
        outcomes: list[str] = []

        def worker(digest: str) -> None:
            barrier.wait()
            try:
                _importer(migration_runtime_engine).import_content(
                    tenant_id,
                    uuid.uuid7(),
                    _candidate(source_resource_id="concurrent-diff", digest=digest),
                    event_context=_event_context(),
                    now=FIXED_NOW,
                )
                outcomes.append("ok")
            except MigrationSourceConflict:
                outcomes.append("conflict")

        threads = [
            threading.Thread(target=worker, args=(DIGEST_A,)),
            threading.Thread(target=worker, args=(DIGEST_B,)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert sorted(outcomes) == ["conflict", "ok"]
        assert _counts(bootstrap_engine, tenant_id=tenant_id)["contents"] == 1

    def test_no_gap_failure_finalization_blocks_changed_digest(
        self, migration_runtime_engine, bootstrap_engine, monkeypatch
    ) -> None:
        tenant_id = uuid.uuid7()
        in_gap = threading.Event()
        release_gap = threading.Event()
        b_outcomes: list[str] = []

        def after_failure() -> None:
            in_gap.set()
            assert release_gap.wait(timeout=15)

        original_insert = SqlAlchemyContentRepository.insert

        def boom(self, content):  # noqa: ANN001
            raise PersistenceOperationFailed("forced content insert failure")

        monkeypatch.setattr(SqlAlchemyContentRepository, "insert", boom)
        importer_a = _importer(
            migration_runtime_engine, after_target_failure=after_failure
        )

        def run_a() -> None:
            with pytest.raises(PersistenceOperationFailed):
                importer_a.import_content(
                    tenant_id,
                    uuid.uuid7(),
                    _candidate(source_resource_id="no-gap", digest=DIGEST_A),
                    event_context=_event_context(),
                    now=FIXED_NOW,
                )

        thread_a = threading.Thread(target=run_a)
        thread_a.start()
        assert in_gap.wait(timeout=15)

        # Target rolled back; FAILED not yet durable; B must not establish D2.
        assert _counts(bootstrap_engine, tenant_id=tenant_id)["contents"] == 0
        assert _counts(bootstrap_engine, tenant_id=tenant_id)["versions"] == 0
        assert _counts(bootstrap_engine, tenant_id=tenant_id)["created"] == 0
        assert _counts(bootstrap_engine, tenant_id=tenant_id)["versioned"] == 0
        assert _mig_row(bootstrap_engine, tenant_id, "no-gap") is None

        def run_b() -> None:
            try:
                _importer(migration_runtime_engine).import_content(
                    tenant_id,
                    uuid.uuid7(),
                    _candidate(source_resource_id="no-gap", digest=DIGEST_B),
                    event_context=_event_context(),
                    now=FIXED_NOW,
                )
                b_outcomes.append("ok")
            except MigrationSourceConflict:
                b_outcomes.append("conflict")

        thread_b = threading.Thread(target=run_b)
        thread_b.start()
        # While A holds the gap, B must still see zero targets.
        threading.Event().wait(0.5)
        assert _counts(bootstrap_engine, tenant_id=tenant_id)["contents"] == 0
        assert _mig_row(bootstrap_engine, tenant_id, "no-gap") is None

        monkeypatch.setattr(SqlAlchemyContentRepository, "insert", original_insert)
        release_gap.set()
        thread_a.join(timeout=15)
        thread_b.join(timeout=15)
        assert not thread_a.is_alive()
        assert not thread_b.is_alive()

        row = _mig_row(bootstrap_engine, tenant_id, "no-gap")
        assert row is not None
        assert row.outcome == "FAILED"
        assert row.source_digest_sha256 == DIGEST_A
        assert row.attempt_count == 1
        assert b_outcomes == ["conflict"]
        assert _counts(bootstrap_engine, tenant_id=tenant_id)["contents"] == 0
        assert _counts(bootstrap_engine, tenant_id=tenant_id)["versions"] == 0
        assert _counts(bootstrap_engine, tenant_id=tenant_id)["created"] == 0
        assert _counts(bootstrap_engine, tenant_id=tenant_id)["versioned"] == 0

        recovered = _importer(migration_runtime_engine).import_content(
            tenant_id,
            uuid.uuid7(),
            _candidate(source_resource_id="no-gap", digest=DIGEST_A),
            event_context=_event_context(),
            now=FIXED_NOW,
        )
        assert recovered.replayed is False
        imported = _mig_row(bootstrap_engine, tenant_id, "no-gap")
        assert imported.outcome == "IMPORTED"
        assert imported.attempt_count == 2
        assert imported.source_digest_sha256 == DIGEST_A
        assert _counts(bootstrap_engine, tenant_id=tenant_id)["contents"] == 1


class TestTrustBoundaryAndPipeline:
    def test_legacy_approved_published_fixture_stays_generated(
        self, migration_runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        result = _importer(migration_runtime_engine).import_content(
            tenant_id,
            uuid.uuid7(),
            _candidate(source_resource_id="legacy-approved"),
            event_context=_event_context(),
            now=FIXED_NOW,
        )
        head = _head(bootstrap_engine, result.content_id.value)
        assert head.stewardship_state == "GENERATED"
        assert head.published_version_id is None
        assert _counts(bootstrap_engine, tenant_id=tenant_id)["reviews"] == 0
        assert _counts(bootstrap_engine, tenant_id=tenant_id)["pubs"] == 0

    def test_import_then_review_approve_publish(
        self, migration_runtime_engine, runtime_engine, bootstrap_engine
    ) -> None:
        from tests.fakes import IDEMPOTENCY_RETENTION

        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        imported = _importer(migration_runtime_engine).import_content(
            tenant_id,
            principal_id,
            _candidate(source_resource_id="pipeline"),
            event_context=_event_context(principal_id),
            now=FIXED_NOW,
        )
        review = ReviewCommandService(
            SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
            AllowReviewAuthorization(),
            AllowReviewCommentPolicy(),
            idempotency_retention=IDEMPOTENCY_RETENTION,
        )
        publish = PublishContentService(
            SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
            AllowPublicationAuthorization(),
            AllowPublicationGovernance(),
            AllowAssetCurrentGovernance(),
            make_test_schema_registry(),
            idempotency_retention=IDEMPOTENCY_RETENTION,
        )
        submitted = review.submit(
            tenant_id,
            principal_id,
            content_id=imported.content_id,
            version_id=imported.version_id,
            expected_aggregate_revision=AggregateRevision(1),
            idempotency_key=str(uuid.uuid7()),
            event_context=_event_context(principal_id),
            audit_provenance=api_mutation_audit_provenance(principal_id),
            now=FIXED_NOW,
        )
        approved = review.approve(
            tenant_id,
            principal_id,
            content_id=imported.content_id,
            version_id=imported.version_id,
            expected_aggregate_revision=submitted.aggregate_revision,
            comment=None,
            reason_code=None,
            idempotency_key=str(uuid.uuid7()),
            event_context=_event_context(principal_id),
            audit_provenance=api_mutation_audit_provenance(principal_id),
            now=FIXED_NOW,
        )
        published = publish.publish(
            tenant_id,
            principal_id,
            content_id=imported.content_id,
            version_id=imported.version_id,
            expected_aggregate_revision=approved.aggregate_revision,
            idempotency_key=str(uuid.uuid7()),
            event_context=_event_context(principal_id),
            audit_provenance=api_mutation_audit_provenance(principal_id),
            now=FIXED_NOW,
        )
        head = _head(bootstrap_engine, imported.content_id.value)
        assert head.published_version_id == imported.version_id.value
        assert published.publication_id is not None


class TestAtomicity:
    def test_content_insert_failure_rolls_back(
        self, migration_runtime_engine, bootstrap_engine, monkeypatch
    ) -> None:
        def boom(self, content):
            raise PersistenceOperationFailed("injected content insert failure")

        monkeypatch.setattr(SqlAlchemyContentRepository, "insert", boom)
        tenant_id = uuid.uuid7()
        with pytest.raises(PersistenceOperationFailed):
            _importer(migration_runtime_engine).import_content(
                tenant_id,
                uuid.uuid7(),
                _candidate(source_resource_id="atom-content"),
                event_context=_event_context(),
                now=FIXED_NOW,
            )
        assert _counts(bootstrap_engine, tenant_id=tenant_id)["contents"] == 0
        row = _mig_row(bootstrap_engine, tenant_id, "atom-content")
        assert row.outcome == "FAILED"

    def test_version_insert_failure_rolls_back(
        self, migration_runtime_engine, bootstrap_engine, monkeypatch
    ) -> None:
        def boom(self, version, provenance):
            raise PersistenceOperationFailed("injected version insert failure")

        monkeypatch.setattr(SqlAlchemyContentVersionRepository, "insert", boom)
        tenant_id = uuid.uuid7()
        with pytest.raises(PersistenceOperationFailed):
            _importer(migration_runtime_engine).import_content(
                tenant_id,
                uuid.uuid7(),
                _candidate(source_resource_id="atom-version"),
                event_context=_event_context(),
                now=FIXED_NOW,
            )
        assert _counts(bootstrap_engine, tenant_id=tenant_id)["contents"] == 0
        assert _mig_row(bootstrap_engine, tenant_id, "atom-version").outcome == "FAILED"

    def test_asset_ref_insert_failure_rolls_back(
        self, migration_runtime_engine, bootstrap_engine, monkeypatch
    ) -> None:
        def boom(self, refs):
            raise PersistenceOperationFailed("injected asset ref failure")

        monkeypatch.setattr(SqlAlchemyVersionAssetRefRepository, "insert_many", boom)
        tenant_id = uuid.uuid7()
        with pytest.raises(PersistenceOperationFailed):
            _importer(migration_runtime_engine).import_content(
                tenant_id,
                uuid.uuid7(),
                _candidate(
                    source_resource_id="atom-assets",
                    asset_refs=(
                        VersionAssetAssociationSpec(
                            resource_ref=ResourceRef("asset.image", uuid.uuid7(), None),
                            role="primary",
                            ordinal=0,
                            required=True,
                        ),
                    ),
                ),
                event_context=_event_context(),
                now=FIXED_NOW,
            )
        assert _counts(bootstrap_engine, tenant_id=tenant_id)["contents"] == 0

    def test_outbox_and_migration_finalization_failures(
        self, migration_runtime_engine, bootstrap_engine, monkeypatch
    ) -> None:
        calls = {"n": 0}
        original_outbox = SqlAlchemyOutboxRepository.insert

        def boom_outbox(self, message):
            calls["n"] += 1
            if calls["n"] == 1:
                raise PersistenceOperationFailed("injected created outbox failure")
            return original_outbox(self, message)

        monkeypatch.setattr(SqlAlchemyOutboxRepository, "insert", boom_outbox)
        tenant_id = uuid.uuid7()
        with pytest.raises(PersistenceOperationFailed):
            _importer(migration_runtime_engine).import_content(
                tenant_id,
                uuid.uuid7(),
                _candidate(source_resource_id="atom-outbox-created"),
                event_context=_event_context(),
                now=FIXED_NOW,
            )
        assert _counts(bootstrap_engine, tenant_id=tenant_id)["contents"] == 0

        calls["n"] = 0

        def boom_version_outbox(self, message):
            calls["n"] += 1
            if calls["n"] == 2:
                raise PersistenceOperationFailed("injected version outbox failure")
            return original_outbox(self, message)

        monkeypatch.setattr(SqlAlchemyOutboxRepository, "insert", boom_version_outbox)
        with pytest.raises(PersistenceOperationFailed):
            _importer(migration_runtime_engine).import_content(
                tenant_id,
                uuid.uuid7(),
                _candidate(source_resource_id="atom-outbox-version"),
                event_context=_event_context(),
                now=FIXED_NOW,
            )
        assert _counts(bootstrap_engine, tenant_id=tenant_id)["contents"] == 0

        def boom_mig(self, record):
            raise PersistenceOperationFailed("injected migration finalization failure")

        monkeypatch.setattr(
            SqlAlchemyMigrationImportRecordRepository, "insert_imported", boom_mig
        )
        with pytest.raises(PersistenceOperationFailed):
            _importer(migration_runtime_engine).import_content(
                tenant_id,
                uuid.uuid7(),
                _candidate(source_resource_id="atom-mig-final"),
                event_context=_event_context(),
                now=FIXED_NOW,
            )
        assert _counts(bootstrap_engine, tenant_id=tenant_id)["contents"] == 0
        assert (
            _mig_row(bootstrap_engine, tenant_id, "atom-mig-final").outcome == "FAILED"
        )

"""GCI-I03 atomic immutable ContentVersion append against PostgreSQL 18."""

from __future__ import annotations

import ast
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.domains.content.application.errors import (
    AggregateRevisionConflict,
    AIProvenanceInvalid,
    ContentNotFound,
    PersistenceInvariantViolation,
    TenantContextMismatch,
    VersionAlreadyExists,
    VersionLineageConflict,
)
from aieos.domains.content.domain.provenance import (
    AIGenerationProvenanceV1,
    ai_generation_provenance_as_json,
)
from aieos.platform.resources import ResourceRef
from aieos.domains.content.application.models import AppendContentVersionCommand
from aieos.domains.content.application.services import AppendContentVersionService
from tests.fakes import AllowAssetReferenceValidation
from aieos.domains.content.domain.identities import (
    AggregateRevision,
    ContentId,
    ContentVersionId,
    VersionNumber,
)
from aieos.domains.content.domain.origin import ContentOrigin
from aieos.domains.content.domain.schema import SchemaId, SchemaVersion
from aieos.domains.content.domain.version import ContentPayload, ContentVersion
from aieos.domains.content.infrastructure.persistence.uow import (
    SqlAlchemyContentUnitOfWorkFactory,
)
from aieos.platform.events.models import MutationEventContext
from tests.dbutil import REPO_ROOT

pytestmark = pytest.mark.gci_i03

FIXED_NOW = datetime(2026, 8, 13, 18, 0, tzinfo=UTC)


def _event_context() -> MutationEventContext:
    actor = uuid.uuid7()
    return MutationEventContext(
        correlation_id=uuid.uuid7(),
        causation_id=uuid.uuid7(),
        actor_principal_id=actor,
        effective_actor_id=actor,
    )


def _service(engine: Engine) -> AppendContentVersionService:
    return AppendContentVersionService(SqlAlchemyContentUnitOfWorkFactory(engine), AllowAssetReferenceValidation())


def _seed_content(
    bootstrap_engine: Engine,
    *,
    tenant_id: uuid.UUID,
    content_id: ContentId | None = None,
    stewardship_state: str = "DRAFT",
    aggregate_revision: int = 0,
    published_version_id: uuid.UUID | None = None,
    current_version_id: uuid.UUID | None = None,
) -> ContentId:
    content_id = content_id or ContentId.generate()
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
                    'Description', 'en-IN', :state, :current_version_id,
                    :published_version_id, :revision, :created_at,
                    :owner, :updated_at, NULL
                )
                """
            ),
            {
                "content_id": content_id.value,
                "tenant_id": tenant_id,
                "owner": uuid.uuid7(),
                "state": stewardship_state,
                "current_version_id": current_version_id,
                "published_version_id": published_version_id,
                "revision": aggregate_revision,
                "created_at": FIXED_NOW,
                "updated_at": FIXED_NOW,
            },
        )
    return content_id


def _make_version(
    *,
    tenant_id: uuid.UUID,
    content_id: ContentId,
    version_number: int,
    parent_version_id: ContentVersionId | None,
    origin: ContentOrigin = ContentOrigin.HUMAN,
    version_id: ContentVersionId | None = None,
    marker: str | None = None,
) -> ContentVersion:
    return ContentVersion(
        version_id=version_id or ContentVersionId.generate(),
        tenant_id=tenant_id,
        content_id=content_id,
        version_number=VersionNumber(version_number),
        parent_version_id=parent_version_id,
        schema_id=SchemaId("test.generic"),
        schema_version=SchemaVersion(1),
        payload=ContentPayload.from_mapping({"marker": marker or f"v{version_number}"}),
        origin=origin,
        created_at=FIXED_NOW,
        created_by_principal_id=uuid.uuid7(),
    )


def _append(
    engine: Engine,
    tenant_id: uuid.UUID,
    version: ContentVersion,
    expected_revision: int,
    provenance: dict[str, object] | None = None,
):
    return _service(engine).append(
        tenant_id,
        AppendContentVersionCommand(
            expected_aggregate_revision=AggregateRevision(expected_revision),
            version=version,
            provenance=provenance,
        ),
        event_context=_event_context(),
        now=FIXED_NOW,
    )


def _content_row(bootstrap_engine: Engine, content_id: ContentId):
    with bootstrap_engine.connect() as conn:
        return conn.execute(
            text(
                """
                SELECT current_version_id, published_version_id, aggregate_revision,
                       stewardship_state, updated_at, title, owner_principal_id
                FROM content.contents WHERE content_id = :cid
                """
            ),
            {"cid": content_id.value},
        ).one()


def _version_count(bootstrap_engine: Engine, content_id: ContentId) -> int:
    with bootstrap_engine.connect() as conn:
        return int(
            conn.execute(
                text(
                    "SELECT count(*) FROM content.content_versions WHERE content_id = :cid"
                ),
                {"cid": content_id.value},
            ).scalar_one()
        )


def _version_numbers(bootstrap_engine: Engine, content_id: ContentId) -> list[int]:
    with bootstrap_engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT version_number FROM content.content_versions
                WHERE content_id = :cid ORDER BY version_number
                """
            ),
            {"cid": content_id.value},
        ).all()
    return [int(row[0]) for row in rows]


class TestFirstAndSubsequentAppend:
    def test_first_version_advances_null_current_and_revision(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        content_id = _seed_content(bootstrap_engine, tenant_id=tenant_id)
        version = _make_version(
            tenant_id=tenant_id, content_id=content_id, version_number=1, parent_version_id=None
        )
        result = _append(runtime_engine, tenant_id, version, 0)
        assert result.content_id == content_id
        assert result.version_id == version.version_id
        assert result.version_number.value == 1
        assert result.aggregate_revision.value == 1
        row = _content_row(bootstrap_engine, content_id)
        assert row.current_version_id == version.version_id.value
        assert row.aggregate_revision == 1
        assert row.published_version_id is None

    def test_second_version_keeps_v1_and_advances_current(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        content_id = _seed_content(bootstrap_engine, tenant_id=tenant_id)
        v1 = _make_version(
            tenant_id=tenant_id, content_id=content_id, version_number=1, parent_version_id=None
        )
        _append(runtime_engine, tenant_id, v1, 0)
        v2 = _make_version(
            tenant_id=tenant_id,
            content_id=content_id,
            version_number=2,
            parent_version_id=v1.version_id,
        )
        result = _append(runtime_engine, tenant_id, v2, 1)
        assert result.aggregate_revision.value == 2
        row = _content_row(bootstrap_engine, content_id)
        assert row.current_version_id == v2.version_id.value
        assert row.aggregate_revision == 2
        factory = SqlAlchemyContentUnitOfWorkFactory(runtime_engine)
        with factory(tenant_id) as uow:
            stored_v1 = uow.versions.get(v1.version_id)
            stored_v2 = uow.versions.get(v2.version_id)
            uow.rollback()
        assert stored_v1 == v1
        assert stored_v2 == v2
        assert _version_numbers(bootstrap_engine, content_id) == [1, 2]


class TestConflictsAndLineage:
    def test_stale_revision_rejected_without_inserting_version(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        content_id = _seed_content(bootstrap_engine, tenant_id=tenant_id)
        v1 = _make_version(
            tenant_id=tenant_id, content_id=content_id, version_number=1, parent_version_id=None
        )
        _append(runtime_engine, tenant_id, v1, 0)
        v2 = _make_version(
            tenant_id=tenant_id,
            content_id=content_id,
            version_number=2,
            parent_version_id=v1.version_id,
        )
        with pytest.raises(AggregateRevisionConflict):
            _append(runtime_engine, tenant_id, v2, 0)
        row = _content_row(bootstrap_engine, content_id)
        assert row.current_version_id == v1.version_id.value
        assert row.aggregate_revision == 1
        assert _version_count(bootstrap_engine, content_id) == 1

    def test_parent_not_current_and_skipped_number_rejected(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        content_id = _seed_content(bootstrap_engine, tenant_id=tenant_id)
        v1 = _make_version(
            tenant_id=tenant_id, content_id=content_id, version_number=1, parent_version_id=None
        )
        _append(runtime_engine, tenant_id, v1, 0)
        v2 = _make_version(
            tenant_id=tenant_id,
            content_id=content_id,
            version_number=2,
            parent_version_id=v1.version_id,
        )
        _append(runtime_engine, tenant_id, v2, 1)
        sibling = _make_version(
            tenant_id=tenant_id,
            content_id=content_id,
            version_number=3,
            parent_version_id=v1.version_id,
        )
        with pytest.raises(VersionLineageConflict):
            _append(runtime_engine, tenant_id, sibling, 2)
        skipped = _make_version(
            tenant_id=tenant_id,
            content_id=content_id,
            version_number=4,
            parent_version_id=v2.version_id,
        )
        with pytest.raises(VersionLineageConflict):
            _append(runtime_engine, tenant_id, skipped, 2)
        assert _version_numbers(bootstrap_engine, content_id) == [1, 2]

    def test_duplicate_version_id_does_not_change_aggregate(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        content_a = _seed_content(bootstrap_engine, tenant_id=tenant_id)
        content_b = _seed_content(bootstrap_engine, tenant_id=tenant_id)
        v1 = _make_version(
            tenant_id=tenant_id, content_id=content_a, version_number=1, parent_version_id=None
        )
        _append(runtime_engine, tenant_id, v1, 0)
        duplicate = _make_version(
            tenant_id=tenant_id,
            content_id=content_b,
            version_number=1,
            parent_version_id=None,
            version_id=v1.version_id,
            marker="dup",
        )
        with pytest.raises(VersionAlreadyExists):
            _append(runtime_engine, tenant_id, duplicate, 0)
        row_a = _content_row(bootstrap_engine, content_a)
        row_b = _content_row(bootstrap_engine, content_b)
        assert row_a.current_version_id == v1.version_id.value
        assert row_a.aggregate_revision == 1
        assert row_b.current_version_id is None
        assert row_b.aggregate_revision == 0
        assert _version_count(bootstrap_engine, content_a) == 1
        assert _version_count(bootstrap_engine, content_b) == 0


class TestTenantIsolation:
    def test_execution_tenant_mismatch_rejected(self, runtime_engine, bootstrap_engine) -> None:
        tenant_a = uuid.uuid7()
        tenant_b = uuid.uuid7()
        content_id = _seed_content(bootstrap_engine, tenant_id=tenant_a)
        version = _make_version(
            tenant_id=tenant_a, content_id=content_id, version_number=1, parent_version_id=None
        )
        with pytest.raises(TenantContextMismatch):
            _append(runtime_engine, tenant_b, version, 0)
        assert _version_count(bootstrap_engine, content_id) == 0

    def test_wrong_tenant_content_is_not_found(self, runtime_engine, bootstrap_engine) -> None:
        tenant_a = uuid.uuid7()
        tenant_b = uuid.uuid7()
        content_id = _seed_content(bootstrap_engine, tenant_id=tenant_b)
        version = _make_version(
            tenant_id=tenant_a, content_id=content_id, version_number=1, parent_version_id=None
        )
        with pytest.raises(ContentNotFound):
            _append(runtime_engine, tenant_a, version, 0)
        missing = _make_version(
            tenant_id=tenant_a,
            content_id=ContentId.generate(),
            version_number=1,
            parent_version_id=None,
        )
        with pytest.raises(ContentNotFound):
            _append(runtime_engine, tenant_a, missing, 0)


class TestProvenanceAndUnchangedFields:
    def test_ai_without_provenance_fails_atomically(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        content_id = _seed_content(bootstrap_engine, tenant_id=tenant_id)
        version = _make_version(
            tenant_id=tenant_id,
            content_id=content_id,
            version_number=1,
            parent_version_id=None,
            origin=ContentOrigin.AI,
        )
        with pytest.raises(AIProvenanceInvalid):
            _append(runtime_engine, tenant_id, version, 0)
        assert _version_count(bootstrap_engine, content_id) == 0
        row = _content_row(bootstrap_engine, content_id)
        assert row.current_version_id is None
        assert row.aggregate_revision == 0

    def test_ai_provenance_object_persists(self, runtime_engine, bootstrap_engine) -> None:
        tenant_id = uuid.uuid7()
        content_id = _seed_content(bootstrap_engine, tenant_id=tenant_id)
        version = _make_version(
            tenant_id=tenant_id,
            content_id=content_id,
            version_number=1,
            parent_version_id=None,
            origin=ContentOrigin.AI,
        )
        provenance = AIGenerationProvenanceV1(
            generation_run_ref=ResourceRef("generation.run", uuid.uuid7(), None),
            prompt_execution_ref=None,
            provider_id="test.provider",
            model_id="test-model-1",
            capability_id="content.generate.lesson",
            source_refs=(),
            policy_refs=(),
            evaluation_refs=(),
            correlation_id=uuid.uuid7(),
        )
        _append(runtime_engine, tenant_id, version, 0, provenance=provenance)
        with bootstrap_engine.connect() as conn:
            stored = conn.execute(
                text(
                    "SELECT provenance FROM content.content_versions WHERE version_id = :vid"
                ),
                {"vid": version.version_id.value},
            ).scalar_one()
        assert stored == ai_generation_provenance_as_json(provenance)

    def test_published_pointer_and_stewardship_unchanged(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        content_id = _seed_content(
            bootstrap_engine, tenant_id=tenant_id, stewardship_state="APPROVED"
        )
        v1 = _make_version(
            tenant_id=tenant_id, content_id=content_id, version_number=1, parent_version_id=None
        )
        _append(runtime_engine, tenant_id, v1, 0)
        with bootstrap_engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE content.contents SET published_version_id = :vid "
                    "WHERE content_id = :cid"
                ),
                {"vid": v1.version_id.value, "cid": content_id.value},
            )
        v2 = _make_version(
            tenant_id=tenant_id,
            content_id=content_id,
            version_number=2,
            parent_version_id=v1.version_id,
        )
        _append(runtime_engine, tenant_id, v2, 1)
        row = _content_row(bootstrap_engine, content_id)
        assert row.published_version_id == v1.version_id.value
        assert row.stewardship_state == "GENERATED"
        assert row.current_version_id == v2.version_id.value
        assert row.title == "Title"


class TestRollbackAndUnitOfWork:
    def test_rollback_after_insert_leaves_no_version(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        content_id = _seed_content(bootstrap_engine, tenant_id=tenant_id)
        version = _make_version(
            tenant_id=tenant_id, content_id=content_id, version_number=1, parent_version_id=None
        )
        factory = SqlAlchemyContentUnitOfWorkFactory(runtime_engine)
        with pytest.raises(RuntimeError, match="test rollback boundary"):
            with factory(tenant_id) as uow:
                uow.versions.insert(version, None)
                raise RuntimeError("test rollback boundary")
        row = _content_row(bootstrap_engine, content_id)
        assert row.current_version_id is None
        assert row.aggregate_revision == 0
        assert _version_count(bootstrap_engine, content_id) == 0

    def test_advance_requires_expected_revision_predicate(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        content_id = _seed_content(bootstrap_engine, tenant_id=tenant_id)
        version = _make_version(
            tenant_id=tenant_id, content_id=content_id, version_number=1, parent_version_id=None
        )
        factory = SqlAlchemyContentUnitOfWorkFactory(runtime_engine)
        with factory(tenant_id) as uow:
            uow.versions.insert(version, None)
            resulting = uow.contents.advance_current_version(
                content_id=content_id,
                tenant_id=tenant_id,
                expected_revision=AggregateRevision(99),
                expected_current_version_id=None,
                expected_state="DRAFT",
                new_version_id=version.version_id,
                updated_at=FIXED_NOW,
            )
            assert resulting is None
            uow.rollback()
        row = _content_row(bootstrap_engine, content_id)
        assert row.current_version_id is None
        assert row.aggregate_revision == 0
        assert _version_count(bootstrap_engine, content_id) == 0

    def test_pooled_uow_does_not_leak_tenant_context(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_a = uuid.uuid7()
        tenant_b = uuid.uuid7()
        content_a = _seed_content(bootstrap_engine, tenant_id=tenant_a)
        content_b = _seed_content(bootstrap_engine, tenant_id=tenant_b)
        _append(
            runtime_engine,
            tenant_a,
            _make_version(
                tenant_id=tenant_a,
                content_id=content_a,
                version_number=1,
                parent_version_id=None,
            ),
            0,
        )
        factory = SqlAlchemyContentUnitOfWorkFactory(runtime_engine)
        with factory(tenant_b) as uow:
            assert uow.contents.get_head_for_update(content_a) is None
            head_b = uow.contents.get_head_for_update(content_b)
            uow.rollback()
        assert head_b is not None
        assert head_b.content_id == content_b


class TestConcurrency:
    def test_two_concurrent_appends_one_success_linear_history(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        content_id = _seed_content(bootstrap_engine, tenant_id=tenant_id)
        v1 = _make_version(
            tenant_id=tenant_id, content_id=content_id, version_number=1, parent_version_id=None
        )
        _append(runtime_engine, tenant_id, v1, 0)
        left = _make_version(
            tenant_id=tenant_id,
            content_id=content_id,
            version_number=2,
            parent_version_id=v1.version_id,
            marker="left",
        )
        right = _make_version(
            tenant_id=tenant_id,
            content_id=content_id,
            version_number=2,
            parent_version_id=v1.version_id,
            marker="right",
        )
        barrier = threading.Barrier(2)
        outcomes: list[str] = []
        winners: list[ContentVersionId] = []
        lock = threading.Lock()
        service = _service(runtime_engine)

        def worker(version: ContentVersion) -> None:
            barrier.wait(timeout=10)
            try:
                service.append(
                    tenant_id,
                    AppendContentVersionCommand(
                        expected_aggregate_revision=AggregateRevision(1),
                        version=version,
                    ),
                    event_context=_event_context(),
                    now=FIXED_NOW,
                )
                with lock:
                    outcomes.append("ok")
                    winners.append(version.version_id)
            except AggregateRevisionConflict:
                with lock:
                    outcomes.append("conflict")

        threads = [
            threading.Thread(target=worker, args=(left,)),
            threading.Thread(target=worker, args=(right,)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        assert outcomes.count("ok") == 1
        assert outcomes.count("conflict") == 1
        assert len(winners) == 1
        row = _content_row(bootstrap_engine, content_id)
        assert row.aggregate_revision == 2
        assert row.current_version_id == winners[0].value
        assert _version_numbers(bootstrap_engine, content_id) == [1, 2]
        assert _version_count(bootstrap_engine, content_id) == 2


@pytest.mark.gci_i05
class TestGetHeadForUpdateLockThenRead:
    def test_waiter_sees_coherent_head_after_committed_first_version(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        content_id = _seed_content(bootstrap_engine, tenant_id=tenant_id)
        v1 = _make_version(
            tenant_id=tenant_id, content_id=content_id, version_number=1, parent_version_id=None
        )
        factory = SqlAlchemyContentUnitOfWorkFactory(runtime_engine)
        locked = threading.Event()
        may_commit = threading.Event()
        observed: list = []
        errors: list[BaseException] = []

        def holder() -> None:
            try:
                with factory(tenant_id) as uow:
                    head = uow.contents.get_head_for_update(content_id)
                    assert head is not None
                    assert head.current_version_id is None
                    locked.set()
                    assert may_commit.wait(timeout=10)
                    uow.versions.insert(v1, None)
                    resulting = uow.contents.advance_current_version(
                        content_id=content_id,
                        tenant_id=tenant_id,
                        expected_revision=AggregateRevision(0),
                        expected_current_version_id=None,
                        expected_state="DRAFT",
                        new_version_id=v1.version_id,
                        updated_at=FIXED_NOW,
                    )
                    assert resulting == AggregateRevision(1)
                    uow.commit()
            except BaseException as exc:
                errors.append(exc)
                locked.set()
                may_commit.set()

        def waiter() -> None:
            try:
                assert locked.wait(timeout=10)
                with factory(tenant_id) as uow:
                    head = uow.contents.get_head_for_update(content_id)
                    observed.append(head)
            except BaseException as exc:
                errors.append(exc)

        holder_thread = threading.Thread(target=holder)
        waiter_thread = threading.Thread(target=waiter)
        holder_thread.start()
        assert locked.wait(timeout=10)
        waiter_thread.start()
        _wait_until_lock_wait(runtime_engine)
        may_commit.set()
        holder_thread.join(timeout=20)
        waiter_thread.join(timeout=20)
        assert errors == []
        assert len(observed) == 1
        head = observed[0]
        assert head is not None
        assert int(head.aggregate_revision) == 1
        assert head.current_version_id == v1.version_id
        assert head.current_version_number is not None
        assert int(head.current_version_number) == 1


def _wait_until_lock_wait(engine: Engine, *, timeout_seconds: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        with engine.connect() as conn:
            waiting = conn.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE wait_event_type = 'Lock' AND pid <> pg_backend_pid()"
                )
            ).scalar_one()
        if int(waiting) >= 1:
            return
        time.sleep(0.02)
    raise AssertionError("waiter did not block on the Content row lock")


class TestArchitectureAndNoSchemaChange:
    def test_repositories_do_not_commit_or_rollback(self) -> None:
        path = (
            REPO_ROOT
            / "src"
            / "aieos"
            / "domains"
            / "content"
            / "infrastructure"
            / "persistence"
            / "repositories.py"
        )
        tree = ast.parse(path.read_text(encoding="utf-8"))
        calls = [
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr in {"commit", "rollback"}
        ]
        assert calls == []

    def test_get_head_for_update_locks_contents_then_reads_version(self) -> None:
        path = (
            REPO_ROOT
            / "src"
            / "aieos"
            / "domains"
            / "content"
            / "infrastructure"
            / "persistence"
            / "repositories.py"
        )
        source = path.read_text(encoding="utf-8")
        start = source.index("def get_head_for_update")
        end = source.index("def advance_current_version")
        body = source[start:end]
        assert "outerjoin" not in body
        assert "with_for_update(of=" not in body
        assert ".with_for_update()" in body
        assert "content_versions_table.c.version_number" in body
        assert "current_version_id has no matching ContentVersion" in body

    def test_no_new_alembic_revision_or_tables(self) -> None:
        versions = sorted(
            path.name
            for path in (REPO_ROOT / "migrations" / "versions").glob("*.py")
            if path.name != "__init__.py"
        )
        assert versions == [
            "adra045001_dispatcher_candidate_authority.py",
            "gcii020001_content_schema.py",
            "gcii050001_api_idempotency.py",
            "gcii060001_review_decisions.py",
            "gcii070001_workflow_intents.py",
            "gcii080001_outbox_messages.py",
            "gcii090001_publications.py",
            "gcii100001_version_asset_refs.py",
            "gcii110001_ai_provenance.py",
            "gcii130001_migration_import.py",
            "pedi090001_security_authority.py",
            "pedi10b2001_asset_authority_sor.py",
            "pedi10b6001_asset_security_audit.py",
            "saii020001_security_audit_ledger.py",
            "tosd020001_teaching_work.py",
            "tosd030001_generation_runs.py",
    "tosd030002_generation_run_work_fence.py",
        ]
        assert not Path(
            REPO_ROOT / "src" / "aieos" / "domains" / "content" / "infrastructure" / "outbox"
        ).exists()

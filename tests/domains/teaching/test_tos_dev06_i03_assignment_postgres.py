"""TOS-DEV06-I03 — PostgreSQL assignment create and publication race tests."""

from __future__ import annotations

import threading
import time
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

from aieos.development.school_context import development_class_authority
from aieos.domains.teaching.application.assignment_create import (
    CreateTeachingAssignmentService,
)
from aieos.domains.teaching.application.audit import api_mutation_audit_provenance
from aieos.domains.teaching.application.errors import (
    ClassRefNotAssignable,
    ContentNotEligibleForAssignment,
    ContentNotFoundForAssignment,
    ContentVersionMismatch,
    IdempotencyKeyReused,
    SchoolContextUnavailable,
)
from aieos.domains.teaching.application.models import CreateTeachingAssignmentCommand
from aieos.domains.teaching.application.school_context import (
    AssignableClassRef,
    SchoolContextClassAuthorityService,
)
from aieos.domains.teaching.infrastructure.persistence.content_eligibility import (
    SqlAlchemyContentAssignmentEligibilityAdapter,
)
from aieos.domains.teaching.infrastructure.persistence.uow import (
    SqlAlchemyTeachingUnitOfWorkFactory,
)
from aieos.platform.events.models import MutationEventContext
from tests.dbutil import set_tenant
from tests.domains.teaching.helpers_dev06_i03 import (
    FIXED_NOW,
    IDEMPOTENCY_RETENTION,
    create_assignment,
    create_service,
    event_context,
    is_lock_contention_error,
    republish_content_to_new_version,
    seed_content_head,
    seed_published_worksheet,
)

pytestmark = pytest.mark.tos_dev06_i03


class TestAssignmentCreatePostgres:
    def test_create_persists_assignment_outbox_and_audit(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id, version_id = seed_published_worksheet(
            bootstrap_engine, tenant_id=tenant_id
        )
        factory = SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine)
        service = CreateTeachingAssignmentService(
            factory,
            development_class_authority(
                tenant_id=tenant_id, teacher_principal_id=principal_id
            ),
            idempotency_retention=IDEMPOTENCY_RETENTION,
        )
        result = service.create(
            tenant_id,
            principal_id,
            CreateTeachingAssignmentCommand(
                content_id=content_id,
                content_version_id=version_id,
                class_ref="class-5a",
            ),
                idempotency_key="i03-create-1",
                event_context=event_context(principal_id),
            audit_provenance=api_mutation_audit_provenance(principal_id),
            now=FIXED_NOW,
        )
        with bootstrap_engine.connect() as conn:
            audit_count = conn.execute(
                text(
                    """
                    SELECT count(*) FROM security.audit_records
                    WHERE tenant_id = :tid
                      AND action = 'teaching.assignment.create'
                    """
                ),
                {"tid": tenant_id},
            ).scalar_one()
            outbox_count = conn.execute(
                text(
                    """
                    SELECT count(*) FROM integration.outbox_messages
                    WHERE tenant_id = :tid
                      AND event_type = 'io.eduvijna.aieos.teaching.assignment.created.v1'
                    """
                ),
                {"tid": tenant_id},
            ).scalar_one()
        assert int(audit_count) == 1
        assert int(outbox_count) == 1
        assert result.class_ref == "class-5a"
        assert result.teacher_principal_id == principal_id

    def test_create_rejects_unpublished_version_after_republish_race(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id, version_v1 = seed_published_worksheet(
            bootstrap_engine, tenant_id=tenant_id
        )
        republish_content_to_new_version(
            bootstrap_engine,
            tenant_id=tenant_id,
            content_id=content_id,
            parent_version_id=version_v1,
            owner_id=uuid.uuid7(),
        )
        factory = SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine)
        service = CreateTeachingAssignmentService(
            factory,
            development_class_authority(
                tenant_id=tenant_id, teacher_principal_id=principal_id
            ),
            idempotency_retention=IDEMPOTENCY_RETENTION,
        )
        with pytest.raises(ContentVersionMismatch):
            service.create(
                tenant_id,
                principal_id,
                CreateTeachingAssignmentCommand(
                    content_id=content_id,
                    content_version_id=version_v1,
                    class_ref="class-5a",
                ),
                idempotency_key="i03-race-a",
                event_context=event_context(principal_id),
                audit_provenance=api_mutation_audit_provenance(principal_id),
                now=datetime(2026, 8, 31, 15, 0, tzinfo=UTC),
            )


class TestPublicationRaceCaseB:
    def test_create_content_lock_blocks_republish_until_commit(
        self,
        runtime_engine: Engine,
        bootstrap_engine: Engine,
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id, version_v1 = seed_published_worksheet(
            bootstrap_engine, tenant_id=tenant_id
        )
        version_v2 = republish_content_to_new_version(
            bootstrap_engine,
            tenant_id=tenant_id,
            content_id=content_id,
            parent_version_id=version_v1,
            owner_id=uuid.uuid7(),
        )
        with bootstrap_engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE content.contents
                    SET published_version_id = :v1, current_version_id = :v2
                    WHERE tenant_id = :tid AND content_id = :cid
                    """
                ),
                {
                    "v1": version_v1,
                    "v2": version_v2,
                    "tid": tenant_id,
                    "cid": content_id,
                },
            )

        lock_acquired = threading.Event()
        republish_finished = threading.Event()
        republish_lock_error: OperationalError | None = None
        holder_pid: int | None = None
        blocking_observed = threading.Event()

        original_verify = (
            SqlAlchemyContentAssignmentEligibilityAdapter.verify_published_learner_content_with_lock
        )

        def patched_verify(self, **kwargs):  # noqa: ANN001
            nonlocal holder_pid
            result = original_verify(self, **kwargs)
            holder_pid = self._contents._connection.execute(  # type: ignore[attr-defined]
                text("SELECT pg_backend_pid()")
            ).scalar_one()
            lock_acquired.set()
            assert republish_finished.wait(timeout=15), (
                "republish never attempted while CREATE held content lock"
            )
            return result

        def republish_worker() -> None:
            nonlocal republish_lock_error
            assert lock_acquired.wait(timeout=15), (
                "CREATE never acquired content head FOR UPDATE lock"
            )
            try:
                with bootstrap_engine.connect() as conn:
                    trans = conn.begin()
                    set_tenant(conn, tenant_id)
                    conn.execute(text("SET LOCAL lock_timeout = '2s'"))
                    conn.execute(
                        text(
                            """
                            UPDATE content.contents
                            SET published_version_id = :v2,
                                current_version_id = :v2,
                                aggregate_revision = aggregate_revision + 1,
                                updated_at = :now
                            WHERE tenant_id = :tid AND content_id = :cid
                            """
                        ),
                        {
                            "v2": version_v2,
                            "tid": tenant_id,
                            "cid": content_id,
                            "now": FIXED_NOW,
                        },
                    )
                    trans.commit()
            except OperationalError as exc:
                republish_lock_error = exc
            finally:
                republish_finished.set()

        def blocking_observer() -> None:
            assert lock_acquired.wait(timeout=15)
            assert holder_pid is not None
            deadline = time.monotonic() + 10.0
            with bootstrap_engine.connect() as conn:
                while time.monotonic() < deadline and not republish_finished.is_set():
                    row = conn.execute(
                        text(
                            """
                            SELECT pid, pg_blocking_pids(pid) AS blockers
                            FROM pg_stat_activity
                            WHERE datname = current_database()
                              AND query LIKE '%UPDATE content.contents%'
                              AND pid <> pg_backend_pid()
                            """
                        )
                    ).mappings().first()
                    if row is not None and row["blockers"]:
                        blockers = list(row["blockers"] or [])
                        if holder_pid in blockers:
                            blocking_observed.set()
                            return
                    time.sleep(0.02)

        factory = SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine)
        service = CreateTeachingAssignmentService(
            factory,
            development_class_authority(
                tenant_id=tenant_id, teacher_principal_id=principal_id
            ),
            idempotency_retention=IDEMPOTENCY_RETENTION,
        )
        republish_thread = threading.Thread(target=republish_worker)
        observer_thread = threading.Thread(target=blocking_observer)
        SqlAlchemyContentAssignmentEligibilityAdapter.verify_published_learner_content_with_lock = (
            patched_verify
        )
        try:
            republish_thread.start()
            observer_thread.start()
            result = service.create(
                tenant_id,
                principal_id,
                CreateTeachingAssignmentCommand(
                    content_id=content_id,
                    content_version_id=version_v1,
                    class_ref="class-5a",
                ),
                idempotency_key="i03-race-b",
                event_context=event_context(principal_id),
                audit_provenance=api_mutation_audit_provenance(principal_id),
                now=FIXED_NOW,
            )
        finally:
            SqlAlchemyContentAssignmentEligibilityAdapter.verify_published_learner_content_with_lock = (
                original_verify
            )
        republish_thread.join(timeout=15)
        observer_thread.join(timeout=15)

        assert republish_lock_error is not None, (
            "conflicting republish UPDATE must fail while CREATE holds row lock"
        )
        assert is_lock_contention_error(republish_lock_error), republish_lock_error
        assert blocking_observed.is_set() or is_lock_contention_error(
            republish_lock_error
        )
        assert result.content_version_id == version_v1

        with bootstrap_engine.connect() as conn:
            published_during = conn.execute(
                text(
                    """
                    SELECT published_version_id FROM content.contents
                    WHERE tenant_id = :tid AND content_id = :cid
                    """
                ),
                {"tid": tenant_id, "cid": content_id},
            ).scalar_one()
        assert published_during == version_v1

        with bootstrap_engine.begin() as conn:
            set_tenant(conn, tenant_id)
            conn.execute(
                text(
                    """
                    UPDATE content.contents
                    SET published_version_id = :v2,
                        current_version_id = :v2,
                        aggregate_revision = aggregate_revision + 1,
                        updated_at = :now
                    WHERE tenant_id = :tid AND content_id = :cid
                    """
                ),
                {
                    "v2": version_v2,
                    "tid": tenant_id,
                    "cid": content_id,
                    "now": FIXED_NOW,
                },
            )

        with bootstrap_engine.connect() as conn:
            published_after = conn.execute(
                text(
                    """
                    SELECT published_version_id FROM content.contents
                    WHERE tenant_id = :tid AND content_id = :cid
                    """
                ),
                {"tid": tenant_id, "cid": content_id},
            ).scalar_one()
        assert published_after == version_v2


class TestPublicationGatesPostgres:
    def test_create_rejects_approved_never_published(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id, version_id = seed_content_head(
            bootstrap_engine,
            tenant_id=tenant_id,
            content_type="worksheet",
            published=False,
        )
        service = create_service(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        )
        with pytest.raises(ContentVersionMismatch):
            service.create(
                tenant_id,
                principal_id,
                CreateTeachingAssignmentCommand(
                    content_id=content_id,
                    content_version_id=version_id,
                    class_ref="class-5a",
                ),
                idempotency_key="i03-gate-unpub",
                event_context=event_context(principal_id),
                audit_provenance=api_mutation_audit_provenance(principal_id),
                now=FIXED_NOW,
            )

    def test_create_rejects_stale_published_version(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id, version_v1 = seed_published_worksheet(
            bootstrap_engine, tenant_id=tenant_id
        )
        republish_content_to_new_version(
            bootstrap_engine,
            tenant_id=tenant_id,
            content_id=content_id,
            parent_version_id=version_v1,
            owner_id=uuid.uuid7(),
        )
        service = create_service(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        )
        with pytest.raises(ContentVersionMismatch):
            service.create(
                tenant_id,
                principal_id,
                CreateTeachingAssignmentCommand(
                    content_id=content_id,
                    content_version_id=version_v1,
                    class_ref="class-5a",
                ),
                idempotency_key="i03-gate-stale",
                event_context=event_context(principal_id),
                audit_provenance=api_mutation_audit_provenance(principal_id),
                now=FIXED_NOW,
            )

    def test_create_rejects_content_version_mismatch(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id, _version_id = seed_published_worksheet(
            bootstrap_engine, tenant_id=tenant_id
        )
        service = create_service(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        )
        with pytest.raises(ContentVersionMismatch):
            service.create(
                tenant_id,
                principal_id,
                CreateTeachingAssignmentCommand(
                    content_id=content_id,
                    content_version_id=uuid.uuid7(),
                    class_ref="class-5a",
                ),
                idempotency_key="i03-gate-mismatch",
                event_context=event_context(principal_id),
                audit_provenance=api_mutation_audit_provenance(principal_id),
                now=FIXED_NOW,
            )

    def test_create_rejects_unknown_content(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        service = create_service(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        )
        with pytest.raises(ContentNotFoundForAssignment):
            service.create(
                tenant_id,
                principal_id,
                CreateTeachingAssignmentCommand(
                    content_id=uuid.uuid7(),
                    content_version_id=uuid.uuid7(),
                    class_ref="class-5a",
                ),
                idempotency_key="i03-gate-unknown",
                event_context=event_context(principal_id),
                audit_provenance=api_mutation_audit_provenance(principal_id),
                now=FIXED_NOW,
            )

    def test_create_rejects_teacher_only_content(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id, version_id = seed_content_head(
            bootstrap_engine,
            tenant_id=tenant_id,
            content_type="lesson_plan",
            published=True,
        )
        service = create_service(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        )
        with pytest.raises(ContentNotEligibleForAssignment):
            service.create(
                tenant_id,
                principal_id,
                CreateTeachingAssignmentCommand(
                    content_id=content_id,
                    content_version_id=version_id,
                    class_ref="class-5a",
                ),
                idempotency_key="i03-gate-teacher",
                event_context=event_context(principal_id),
                audit_provenance=api_mutation_audit_provenance(principal_id),
                now=FIXED_NOW,
            )


class _MutableSchoolContextReader:
    def __init__(
        self,
        *,
        tenant_id: uuid.UUID,
        teacher_principal_id: uuid.UUID,
        items: tuple[AssignableClassRef, ...],
    ) -> None:
        self._tenant_id = tenant_id
        self._teacher_principal_id = teacher_principal_id
        self._items = items
        self._unavailable = False
        self.call_count = 0

    def revoke_all(self) -> None:
        self._items = ()

    def set_unavailable(self) -> None:
        self._unavailable = True

    def list_assignable_classes(
        self,
        tenant_id: uuid.UUID,
        teacher_principal_id: uuid.UUID,
    ) -> tuple[AssignableClassRef, ...]:
        self.call_count += 1
        if self._unavailable:
            raise SchoolContextUnavailable("School Context is temporarily unavailable")
        if (
            tenant_id != self._tenant_id
            or teacher_principal_id != self._teacher_principal_id
        ):
            return ()
        return self._items


class TestCreateIdempotencyReplayOrder:
    def test_replay_survives_school_context_unavailable(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id, version_id = seed_published_worksheet(
            bootstrap_engine, tenant_id=tenant_id
        )
        reader = _MutableSchoolContextReader(
            tenant_id=tenant_id,
            teacher_principal_id=principal_id,
            items=(AssignableClassRef(class_ref="class-5a", display_label="Grade 5A"),),
        )
        authority = SchoolContextClassAuthorityService(reader)
        service = CreateTeachingAssignmentService(
            SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine),
            authority,
            idempotency_retention=IDEMPOTENCY_RETENTION,
        )
        command = CreateTeachingAssignmentCommand(
            content_id=content_id,
            content_version_id=version_id,
            class_ref="class-5a",
        )
        first = service.create(
            tenant_id,
            principal_id,
            command,
            idempotency_key="i03-replay-unavail",
            event_context=event_context(principal_id),
            audit_provenance=api_mutation_audit_provenance(principal_id),
            now=FIXED_NOW,
        )
        reader.set_unavailable()
        replay = service.create(
            tenant_id,
            principal_id,
            command,
            idempotency_key="i03-replay-unavail",
            event_context=event_context(principal_id),
            audit_provenance=api_mutation_audit_provenance(principal_id),
            now=FIXED_NOW,
        )
        assert replay.assignment_id == first.assignment_id
        assert reader.call_count == 1

    def test_replay_survives_class_ref_revocation(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id, version_id = seed_published_worksheet(
            bootstrap_engine, tenant_id=tenant_id
        )
        reader = _MutableSchoolContextReader(
            tenant_id=tenant_id,
            teacher_principal_id=principal_id,
            items=(AssignableClassRef(class_ref="class-5a", display_label="Grade 5A"),),
        )
        authority = SchoolContextClassAuthorityService(reader)
        service = CreateTeachingAssignmentService(
            SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine),
            authority,
            idempotency_retention=IDEMPOTENCY_RETENTION,
        )
        command = CreateTeachingAssignmentCommand(
            content_id=content_id,
            content_version_id=version_id,
            class_ref="class-5a",
        )
        first = service.create(
            tenant_id,
            principal_id,
            command,
            idempotency_key="i03-replay-revoke",
            event_context=event_context(principal_id),
            audit_provenance=api_mutation_audit_provenance(principal_id),
            now=FIXED_NOW,
        )
        reader.revoke_all()
        replay = service.create(
            tenant_id,
            principal_id,
            command,
            idempotency_key="i03-replay-revoke",
            event_context=event_context(principal_id),
            audit_provenance=api_mutation_audit_provenance(principal_id),
            now=FIXED_NOW,
        )
        assert replay.assignment_id == first.assignment_id
        assert reader.call_count == 1

    def test_same_key_changed_fingerprint_conflicts(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id, version_id = seed_published_worksheet(
            bootstrap_engine, tenant_id=tenant_id
        )
        service = CreateTeachingAssignmentService(
            SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine),
            development_class_authority(
                tenant_id=tenant_id, teacher_principal_id=principal_id
            ),
            idempotency_retention=IDEMPOTENCY_RETENTION,
        )
        service.create(
            tenant_id,
            principal_id,
            CreateTeachingAssignmentCommand(
                content_id=content_id,
                content_version_id=version_id,
                class_ref="class-5a",
            ),
            idempotency_key="i03-replay-conflict",
            event_context=event_context(principal_id),
            audit_provenance=api_mutation_audit_provenance(principal_id),
            now=FIXED_NOW,
        )
        with pytest.raises(IdempotencyKeyReused):
            service.create(
                tenant_id,
                principal_id,
                CreateTeachingAssignmentCommand(
                    content_id=content_id,
                    content_version_id=version_id,
                    class_ref="class-5b",
                ),
                idempotency_key="i03-replay-conflict",
                event_context=event_context(principal_id),
                audit_provenance=api_mutation_audit_provenance(principal_id),
                now=FIXED_NOW,
            )

    def test_fresh_key_after_revocation_still_fails_authority(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id, version_id = seed_published_worksheet(
            bootstrap_engine, tenant_id=tenant_id
        )
        reader = _MutableSchoolContextReader(
            tenant_id=tenant_id,
            teacher_principal_id=principal_id,
            items=(AssignableClassRef(class_ref="class-5a", display_label="Grade 5A"),),
        )
        authority = SchoolContextClassAuthorityService(reader)
        service = CreateTeachingAssignmentService(
            SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine),
            authority,
            idempotency_retention=IDEMPOTENCY_RETENTION,
        )
        reader.revoke_all()
        with pytest.raises(ClassRefNotAssignable):
            service.create(
                tenant_id,
                principal_id,
                CreateTeachingAssignmentCommand(
                    content_id=content_id,
                    content_version_id=version_id,
                    class_ref="class-5a",
                ),
                idempotency_key="i03-fresh-after-revoke",
                event_context=event_context(principal_id),
                audit_provenance=api_mutation_audit_provenance(principal_id),
                now=FIXED_NOW,
            )

    def test_same_key_same_fingerprint_returns_same_assignment_id(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id, version_id = seed_published_worksheet(
            bootstrap_engine, tenant_id=tenant_id
        )
        command = CreateTeachingAssignmentCommand(
            content_id=content_id,
            content_version_id=version_id,
            class_ref="class-5a",
        )
        service = create_service(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        )
        first = service.create(
            tenant_id,
            principal_id,
            command,
            idempotency_key="i03-same-key-fp",
            event_context=event_context(principal_id),
            audit_provenance=api_mutation_audit_provenance(principal_id),
            now=FIXED_NOW,
        )
        second = service.create(
            tenant_id,
            principal_id,
            command,
            idempotency_key="i03-same-key-fp",
            event_context=event_context(principal_id),
            audit_provenance=api_mutation_audit_provenance(principal_id),
            now=FIXED_NOW,
        )
        assert second.assignment_id == first.assignment_id

    def test_different_keys_same_business_fields_yield_two_assignments(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id, version_id = seed_published_worksheet(
            bootstrap_engine, tenant_id=tenant_id
        )
        command = CreateTeachingAssignmentCommand(
            content_id=content_id,
            content_version_id=version_id,
            class_ref="class-5a",
        )
        service = create_service(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        )
        first = service.create(
            tenant_id,
            principal_id,
            command,
            idempotency_key="i03-diff-key-a",
            event_context=event_context(principal_id),
            audit_provenance=api_mutation_audit_provenance(principal_id),
            now=FIXED_NOW,
        )
        second = service.create(
            tenant_id,
            principal_id,
            command,
            idempotency_key="i03-diff-key-b",
            event_context=event_context(principal_id),
            audit_provenance=api_mutation_audit_provenance(principal_id),
            now=FIXED_NOW,
        )
        assert second.assignment_id != first.assignment_id

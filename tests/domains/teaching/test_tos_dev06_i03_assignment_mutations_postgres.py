"""TOS-DEV06-I03 — TeachingAssignment lifecycle mutation PostgreSQL tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.engine import Engine

from aieos.domains.teaching.application.assignment_mutations import (
    CancelTeachingAssignmentService,
    CloseTeachingAssignmentService,
    UpdateTeachingAssignmentDueService,
)
from aieos.domains.teaching.application.audit import api_mutation_audit_provenance
from aieos.domains.teaching.application.errors import (
    AggregateRevisionConflict,
    IdempotencyKeyReused,
    TeachingAssignmentNotActive,
)
from aieos.domains.teaching.application.models import UpdateTeachingAssignmentDueCommand
from aieos.domains.teaching.domain.identities import AggregateRevision, AssignmentId
from aieos.domains.teaching.infrastructure.persistence.uow import (
    SqlAlchemyTeachingUnitOfWorkFactory,
)
from aieos.platform.events.constants import (
    EVENT_TEACHING_ASSIGNMENT_CANCELLED_V1,
    EVENT_TEACHING_ASSIGNMENT_CLOSED_V1,
    EVENT_TEACHING_ASSIGNMENT_DUE_UPDATED_V1,
)
from tests.domains.teaching.helpers_dev06_i03 import (
    FIXED_NOW,
    IDEMPOTENCY_RETENTION,
    create_assignment,
    event_context,
    fetch_audit,
    fetch_outbox,
    seed_published_worksheet,
)

pytestmark = pytest.mark.tos_dev06_i03

DUE_AT = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)


def _due_service(runtime_engine: Engine) -> UpdateTeachingAssignmentDueService:
    return UpdateTeachingAssignmentDueService(
        SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine),
        idempotency_retention=IDEMPOTENCY_RETENTION,
    )


def _close_service(runtime_engine: Engine) -> CloseTeachingAssignmentService:
    return CloseTeachingAssignmentService(
        SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine),
        idempotency_retention=IDEMPOTENCY_RETENTION,
    )


def _cancel_service(runtime_engine: Engine) -> CancelTeachingAssignmentService:
    return CancelTeachingAssignmentService(
        SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine),
        idempotency_retention=IDEMPOTENCY_RETENTION,
    )


class TestDueUpdatePostgres:
    def test_due_update_persists_outbox_and_audit(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id, version_id = seed_published_worksheet(
            bootstrap_engine, tenant_id=tenant_id
        )
        created = create_assignment(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            content_id=content_id,
            content_version_id=version_id,
            idempotency_key="i03-mut-due-create",
        )
        service = _due_service(runtime_engine)
        updated = service.update_due(
            tenant_id,
            principal_id,
            assignment_id=created.assignment_id,
            expected_aggregate_revision=AggregateRevision(0),
            command=UpdateTeachingAssignmentDueCommand(due_at=DUE_AT),
            idempotency_key="i03-mut-due",
            event_context=event_context(principal_id),
            audit_provenance=api_mutation_audit_provenance(principal_id),
            now=FIXED_NOW,
        )
        assert int(updated.aggregate_revision) == 1
        assert updated.due_at == DUE_AT
        outbox = fetch_outbox(
            bootstrap_engine,
            tenant_id=tenant_id,
            event_type=EVENT_TEACHING_ASSIGNMENT_DUE_UPDATED_V1,
            assignment_id=created.assignment_id,
        )
        assert len(outbox) == 1
        assert outbox[0]["aggregate_revision"] == 1
        audit = fetch_audit(
            bootstrap_engine,
            tenant_id=tenant_id,
            action="teaching.assignment.due_update",
            assignment_id=created.assignment_id,
        )
        assert len(audit) == 1
        assert audit[0]["resource_revision_before"] == 0
        assert audit[0]["resource_revision_after"] == 1

    def test_due_update_stale_revision_raises(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id, version_id = seed_published_worksheet(
            bootstrap_engine, tenant_id=tenant_id
        )
        created = create_assignment(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            content_id=content_id,
            content_version_id=version_id,
            idempotency_key="i03-mut-due-stale-create",
        )
        service = _due_service(runtime_engine)
        with pytest.raises(AggregateRevisionConflict):
            service.update_due(
                tenant_id,
                principal_id,
                assignment_id=created.assignment_id,
                expected_aggregate_revision=AggregateRevision(99),
                command=UpdateTeachingAssignmentDueCommand(due_at=DUE_AT),
                idempotency_key="i03-mut-due-stale",
                event_context=event_context(principal_id),
                audit_provenance=api_mutation_audit_provenance(principal_id),
                now=FIXED_NOW,
            )

    def test_due_update_idempotency_conflict(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id, version_id = seed_published_worksheet(
            bootstrap_engine, tenant_id=tenant_id
        )
        created = create_assignment(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            content_id=content_id,
            content_version_id=version_id,
            idempotency_key="i03-mut-due-conf-create",
        )
        service = _due_service(runtime_engine)
        service.update_due(
            tenant_id,
            principal_id,
            assignment_id=created.assignment_id,
            expected_aggregate_revision=AggregateRevision(0),
            command=UpdateTeachingAssignmentDueCommand(due_at=DUE_AT),
            idempotency_key="i03-mut-due-conf",
            event_context=event_context(principal_id),
            audit_provenance=api_mutation_audit_provenance(principal_id),
            now=FIXED_NOW,
        )
        with pytest.raises(IdempotencyKeyReused):
            service.update_due(
                tenant_id,
                principal_id,
                assignment_id=created.assignment_id,
                expected_aggregate_revision=AggregateRevision(1),
                command=UpdateTeachingAssignmentDueCommand(
                    due_at=datetime(2026, 10, 1, tzinfo=UTC)
                ),
                idempotency_key="i03-mut-due-conf",
                event_context=event_context(principal_id),
                audit_provenance=api_mutation_audit_provenance(principal_id),
                now=FIXED_NOW,
            )


class TestClosePostgres:
    def test_close_persists_outbox_and_audit(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id, version_id = seed_published_worksheet(
            bootstrap_engine, tenant_id=tenant_id
        )
        created = create_assignment(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            content_id=content_id,
            content_version_id=version_id,
            idempotency_key="i03-mut-close-create",
        )
        service = _close_service(runtime_engine)
        closed = service.close(
            tenant_id,
            principal_id,
            assignment_id=created.assignment_id,
            expected_aggregate_revision=AggregateRevision(0),
            idempotency_key="i03-mut-close",
            event_context=event_context(principal_id),
            audit_provenance=api_mutation_audit_provenance(principal_id),
            now=FIXED_NOW,
        )
        assert closed.lifecycle_state == "CLOSED"
        assert closed.closed_at == FIXED_NOW
        assert int(closed.aggregate_revision) == 1
        outbox = fetch_outbox(
            bootstrap_engine,
            tenant_id=tenant_id,
            event_type=EVENT_TEACHING_ASSIGNMENT_CLOSED_V1,
            assignment_id=created.assignment_id,
        )
        assert len(outbox) == 1
        audit = fetch_audit(
            bootstrap_engine,
            tenant_id=tenant_id,
            action="teaching.assignment.close",
            assignment_id=created.assignment_id,
        )
        assert len(audit) == 1

    def test_close_after_closed_is_terminal(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id, version_id = seed_published_worksheet(
            bootstrap_engine, tenant_id=tenant_id
        )
        created = create_assignment(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            content_id=content_id,
            content_version_id=version_id,
            idempotency_key="i03-mut-close-term-create",
        )
        close_service = _close_service(runtime_engine)
        close_service.close(
            tenant_id,
            principal_id,
            assignment_id=created.assignment_id,
            expected_aggregate_revision=AggregateRevision(0),
            idempotency_key="i03-mut-close-term",
            event_context=event_context(principal_id),
            audit_provenance=api_mutation_audit_provenance(principal_id),
            now=FIXED_NOW,
        )
        with pytest.raises(TeachingAssignmentNotActive):
            close_service.close(
                tenant_id,
                principal_id,
                assignment_id=created.assignment_id,
                expected_aggregate_revision=AggregateRevision(1),
                idempotency_key="i03-mut-close-again",
                event_context=event_context(principal_id),
                audit_provenance=api_mutation_audit_provenance(principal_id),
                now=FIXED_NOW,
            )


class TestCancelPostgres:
    def test_cancel_persists_outbox_and_audit(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id, version_id = seed_published_worksheet(
            bootstrap_engine, tenant_id=tenant_id
        )
        created = create_assignment(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            content_id=content_id,
            content_version_id=version_id,
            idempotency_key="i03-mut-cancel-create",
        )
        service = _cancel_service(runtime_engine)
        cancelled = service.cancel(
            tenant_id,
            principal_id,
            assignment_id=created.assignment_id,
            expected_aggregate_revision=AggregateRevision(0),
            idempotency_key="i03-mut-cancel",
            event_context=event_context(principal_id),
            audit_provenance=api_mutation_audit_provenance(principal_id),
            now=FIXED_NOW,
        )
        assert cancelled.lifecycle_state == "CANCELLED"
        assert cancelled.cancelled_at == FIXED_NOW
        outbox = fetch_outbox(
            bootstrap_engine,
            tenant_id=tenant_id,
            event_type=EVENT_TEACHING_ASSIGNMENT_CANCELLED_V1,
            assignment_id=created.assignment_id,
        )
        assert len(outbox) == 1
        audit = fetch_audit(
            bootstrap_engine,
            tenant_id=tenant_id,
            action="teaching.assignment.cancel",
            assignment_id=created.assignment_id,
        )
        assert len(audit) == 1

    def test_cancel_after_cancelled_is_terminal(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id, version_id = seed_published_worksheet(
            bootstrap_engine, tenant_id=tenant_id
        )
        created = create_assignment(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            content_id=content_id,
            content_version_id=version_id,
            idempotency_key="i03-mut-cancel-term-create",
        )
        cancel_service = _cancel_service(runtime_engine)
        cancel_service.cancel(
            tenant_id,
            principal_id,
            assignment_id=created.assignment_id,
            expected_aggregate_revision=AggregateRevision(0),
            idempotency_key="i03-mut-cancel-term",
            event_context=event_context(principal_id),
            audit_provenance=api_mutation_audit_provenance(principal_id),
            now=FIXED_NOW,
        )
        with pytest.raises(TeachingAssignmentNotActive):
            cancel_service.cancel(
                tenant_id,
                principal_id,
                assignment_id=created.assignment_id,
                expected_aggregate_revision=AggregateRevision(1),
                idempotency_key="i03-mut-cancel-again",
                event_context=event_context(principal_id),
                audit_provenance=api_mutation_audit_provenance(principal_id),
                now=FIXED_NOW,
            )

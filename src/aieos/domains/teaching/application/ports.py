"""Teaching persistence ports. Infrastructure types are not part of this contract."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from aieos.domains.teaching.domain.assignment import TeachingAssignment
from aieos.domains.teaching.domain.identities import (
    AggregateRevision,
    AssignmentId,
    WorkId,
)
from aieos.domains.teaching.domain.work import TeachingWork
from aieos.platform.events.ports import OutboxRepository
from aieos.platform.idempotency.ports import IdempotencyRepository
from aieos.platform.security.audit.ports import SecurityMutationAuditRepository

from aieos.domains.teaching.application.content_eligibility import (
    ContentAssignmentEligibilityPort,
)

TEACHING_WORK_CREATE = "teaching.work.create"
TEACHING_WORK_REFINE = "teaching.work.refine"
TEACHING_ASSIGNMENT_CREATE = "teaching.assignment.create"
TEACHING_ASSIGNMENT_DUE_UPDATE = "teaching.assignment.due_update"
TEACHING_ASSIGNMENT_CLOSE = "teaching.assignment.close"
TEACHING_ASSIGNMENT_CANCEL = "teaching.assignment.cancel"


class TeachingWorkRepository(Protocol):
    """Durable persistence for the teacher-owned TeachingWork aggregate."""

    def insert(self, work: TeachingWork) -> None: ...

    def get(self, work_id: WorkId) -> TeachingWork | None: ...

    def get_for_update(self, work_id: WorkId) -> TeachingWork | None: ...

    def update(
        self,
        work: TeachingWork,
        *,
        expected_revision: AggregateRevision,
    ) -> bool: ...

    def list_for_teacher(
        self,
        *,
        teacher_principal_id: UUID,
        limit: int,
        include_archived: bool,
    ) -> list[TeachingWork]: ...

    def most_recently_updated_for_teacher(
        self,
        *,
        teacher_principal_id: UUID,
    ) -> TeachingWork | None: ...

    def count_active_for_teacher(self, *, teacher_principal_id: UUID) -> int: ...


class TeachingAssignmentRepository(Protocol):
    """Durable persistence for the teacher-owned TeachingAssignment aggregate."""

    def insert(self, assignment: TeachingAssignment) -> None: ...

    def get(self, assignment_id: AssignmentId) -> TeachingAssignment | None: ...

    def get_for_update(
        self, assignment_id: AssignmentId
    ) -> TeachingAssignment | None: ...

    def update(
        self,
        assignment: TeachingAssignment,
        *,
        expected_revision: AggregateRevision,
    ) -> bool: ...

    def list_for_teacher(
        self,
        *,
        teacher_principal_id: UUID,
        limit: int,
        lifecycle_state: str | None = None,
    ) -> list[TeachingAssignment]: ...


class ReviewQueuePendingCountPort(Protocol):
    """Pending Review Queue size for the current teacher in the execution tenant.

    Teaching does not own the Review Queue. This port exists so the Mission
    projection can compose a count without importing Content persistence.
    """

    def pending_count(
        self, execution_tenant_id: UUID, principal_id: UUID
    ) -> int: ...


class TeachingClock(Protocol):
    def now(self) -> datetime: ...


class TeachingUnitOfWork(Protocol):
    works: TeachingWorkRepository
    assignments: TeachingAssignmentRepository
    idempotency: IdempotencyRepository
    outbox: OutboxRepository
    audit: SecurityMutationAuditRepository
    content_eligibility: ContentAssignmentEligibilityPort

    def __enter__(self) -> TeachingUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object,
    ) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


class TeachingUnitOfWorkFactory(Protocol):
    def __call__(self, execution_tenant_id: UUID) -> TeachingUnitOfWork: ...

"""Teaching persistence ports. Infrastructure types are not part of this contract."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from aieos.domains.teaching.domain.assignment import TeachingAssignment
from aieos.domains.teaching.domain.execution import TeachingExecution
from aieos.domains.teaching.domain.execution_content_binding import (
    TeachingExecutionContentBinding,
)
from aieos.domains.teaching.domain.execution_observation import (
    TeachingExecutionObservation,
)
from aieos.domains.teaching.domain.identities import (
    AggregateRevision,
    AssignmentId,
    ExecutionId,
    ObservationId,
    ObservationRevision,
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
TEACHING_EXECUTION_START = "teaching.execution.start"
TEACHING_EXECUTION_COMPLETE = "teaching.execution.complete"
TEACHING_EXECUTION_CANCEL = "teaching.execution.cancel"
TEACHING_EXECUTION_OBSERVATION_CREATE = "teaching.execution.observation.create"
TEACHING_EXECUTION_OBSERVATION_CORRECT = "teaching.execution.observation.correct"


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


class TeachingExecutionRepository(Protocol):
    """Durable persistence for TeachingExecution and its conceptual children."""

    def insert(self, execution: TeachingExecution) -> None: ...

    def get(self, execution_id: ExecutionId) -> TeachingExecution | None: ...

    def get_for_update(
        self, execution_id: ExecutionId
    ) -> TeachingExecution | None: ...

    def update(
        self,
        execution: TeachingExecution,
        *,
        expected_revision: AggregateRevision,
    ) -> bool: ...

    def list_bindings(
        self, execution_id: ExecutionId
    ) -> list[TeachingExecutionContentBinding]: ...

    def insert_observation(
        self, observation: TeachingExecutionObservation
    ) -> None: ...

    def get_observation(
        self, observation_id: ObservationId
    ) -> TeachingExecutionObservation | None: ...

    def list_observations(
        self, execution_id: ExecutionId
    ) -> list[TeachingExecutionObservation]: ...

    def update_observation(
        self,
        observation: TeachingExecutionObservation,
        *,
        expected_revision: ObservationRevision,
    ) -> bool: ...

    def list_for_teacher(
        self,
        *,
        teacher_principal_id: UUID,
        limit: int,
        work_id: WorkId | None = None,
        class_ref: str | None = None,
        lifecycle_state: str | None = None,
    ) -> list[TeachingExecution]: ...


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
    executions: TeachingExecutionRepository
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

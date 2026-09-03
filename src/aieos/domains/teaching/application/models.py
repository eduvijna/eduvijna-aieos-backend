"""Application command/query/result contracts for Teaching Work."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from aieos.domains.teaching.domain.assignment import TeachingAssignment
from aieos.domains.teaching.domain.execution import TeachingExecution
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
from aieos.domains.teaching.domain.work import UNSET, TeachingWork, UnsetType


@dataclass(frozen=True, slots=True)
class CreateTeachingWorkCommand:
    """A Teaching Intent request entering Work creation.

    This is the transient intent payload. It is never persisted as its own
    aggregate; only the resulting TeachingWork is durable.
    """

    intent_type: str
    goal_text: str
    target_date: date
    locale: str
    class_label: str | None = None
    subject: str | None = None
    topic: str | None = None


@dataclass(frozen=True, slots=True)
class RefineTeachingWorkCommand:
    """PATCH-style partial refinement of an existing TeachingWork.

    UNSET means "field omitted"; None means "explicitly cleared" for the
    nullable contextual fields.
    """

    goal_text: str | UnsetType = UNSET
    class_label: str | None | UnsetType = UNSET
    subject: str | None | UnsetType = UNSET
    topic: str | None | UnsetType = UNSET
    target_date: date | UnsetType = UNSET
    locale: str | UnsetType = UNSET

    def has_changes(self) -> bool:
        return any(
            not isinstance(value, UnsetType)
            for value in (
                self.goal_text,
                self.class_label,
                self.subject,
                self.topic,
                self.target_date,
                self.locale,
            )
        )


@dataclass(frozen=True, slots=True)
class ListTeachingWorksQuery:
    limit: int
    include_archived: bool = False


@dataclass(frozen=True, slots=True)
class TeachingWorkReadModel:
    work_id: WorkId
    intent_type: str
    goal_text: str
    class_label: str | None
    subject: str | None
    topic: str | None
    target_date: date
    locale: str
    aggregate_revision: AggregateRevision
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None


@dataclass(frozen=True, slots=True)
class ListTeachingWorksResult:
    items: tuple[TeachingWorkReadModel, ...]
    has_more: bool


def teaching_work_read_model(work: TeachingWork) -> TeachingWorkReadModel:
    return TeachingWorkReadModel(
        work_id=work.work_id,
        intent_type=work.intent_type.value,
        goal_text=work.goal_text,
        class_label=work.class_label,
        subject=work.subject,
        topic=work.topic,
        target_date=work.target_date,
        locale=work.locale,
        aggregate_revision=work.aggregate_revision,
        created_at=work.created_at,
        updated_at=work.updated_at,
        archived_at=work.archived_at,
    )


@dataclass(frozen=True, slots=True)
class CreateTeachingAssignmentCommand:
    content_id: UUID
    content_version_id: UUID
    class_ref: str
    source_work_id: UUID | None = None
    available_from: datetime | None = None
    due_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class UpdateTeachingAssignmentDueCommand:
    due_at: datetime | None


@dataclass(frozen=True, slots=True)
class ListTeachingAssignmentsQuery:
    limit: int
    lifecycle_state: str | None = None


@dataclass(frozen=True, slots=True)
class TeachingAssignmentReadModel:
    assignment_id: AssignmentId
    teacher_principal_id: UUID
    content_id: UUID
    content_version_id: UUID
    audience_type: str
    class_ref: str
    audience_display_label: str | None
    source_work_id: WorkId | None
    lifecycle_state: str
    assigned_at: datetime
    available_from: datetime
    due_at: datetime | None
    closed_at: datetime | None
    cancelled_at: datetime | None
    aggregate_revision: AggregateRevision
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ListTeachingAssignmentsResult:
    items: tuple[TeachingAssignmentReadModel, ...]
    has_more: bool


def teaching_assignment_read_model(
    assignment: TeachingAssignment,
) -> TeachingAssignmentReadModel:
    return TeachingAssignmentReadModel(
        assignment_id=assignment.assignment_id,
        teacher_principal_id=assignment.teacher_principal_id,
        content_id=assignment.content_id,
        content_version_id=assignment.content_version_id,
        audience_type=assignment.audience_type.value,
        class_ref=assignment.class_ref,
        audience_display_label=assignment.audience_display_label,
        source_work_id=assignment.source_work_id,
        lifecycle_state=assignment.lifecycle_state.value,
        assigned_at=assignment.assigned_at,
        available_from=assignment.available_from,
        due_at=assignment.due_at,
        closed_at=assignment.closed_at,
        cancelled_at=assignment.cancelled_at,
        aggregate_revision=assignment.aggregate_revision,
        created_at=assignment.created_at,
        updated_at=assignment.updated_at,
    )


@dataclass(frozen=True, slots=True)
class TeachingExecutionContentBindingInput:
    content_id: UUID
    content_version_id: UUID
    artifact_kind: str


@dataclass(frozen=True, slots=True)
class StartTeachingExecutionCommand:
    work_id: UUID
    class_ref: str
    bindings: tuple[TeachingExecutionContentBindingInput, ...] = ()


@dataclass(frozen=True, slots=True)
class CreateTeachingExecutionObservationCommand:
    observation_kind: str
    body: str


@dataclass(frozen=True, slots=True)
class CorrectTeachingExecutionObservationCommand:
    body: str


@dataclass(frozen=True, slots=True)
class ListTeachingExecutionsQuery:
    limit: int
    work_id: UUID | None = None
    class_ref: str | None = None
    lifecycle_state: str | None = None


@dataclass(frozen=True, slots=True)
class GetTeacherOsTeachContextQuery:
    work_id: UUID
    class_ref: str


@dataclass(frozen=True, slots=True)
class TeachingExecutionContentBindingReadModel:
    content_id: UUID
    content_version_id: UUID
    artifact_kind: str


@dataclass(frozen=True, slots=True)
class TeachingExecutionReadModel:
    execution_id: ExecutionId
    teacher_principal_id: UUID
    work_id: WorkId
    class_ref: str
    lifecycle_state: str
    started_at: datetime
    completed_at: datetime | None
    cancelled_at: datetime | None
    aggregate_revision: AggregateRevision
    created_at: datetime
    updated_at: datetime
    bindings: tuple[TeachingExecutionContentBindingReadModel, ...]
    observations: tuple[TeachingExecutionObservationReadModel, ...] = ()


@dataclass(frozen=True, slots=True)
class ListTeachingExecutionsResult:
    items: tuple[TeachingExecutionReadModel, ...]
    has_more: bool


@dataclass(frozen=True, slots=True)
class TeachingExecutionObservationReadModel:
    observation_id: ObservationId
    execution_id: ExecutionId
    observation_kind: str
    body: str
    recorded_at: datetime
    updated_at: datetime
    revision: ObservationRevision


def teaching_execution_observation_read_model(
    observation: TeachingExecutionObservation,
) -> TeachingExecutionObservationReadModel:
    return TeachingExecutionObservationReadModel(
        observation_id=observation.observation_id,
        execution_id=observation.execution_id,
        observation_kind=observation.observation_kind.value,
        body=observation.body,
        recorded_at=observation.recorded_at,
        updated_at=observation.updated_at,
        revision=observation.revision,
    )


def teaching_execution_read_model(
    execution: TeachingExecution,
    *,
    observations: tuple[TeachingExecutionObservation, ...] = (),
) -> TeachingExecutionReadModel:
    return TeachingExecutionReadModel(
        execution_id=execution.execution_id,
        teacher_principal_id=execution.teacher_principal_id,
        work_id=execution.work_id,
        class_ref=execution.class_ref,
        lifecycle_state=execution.lifecycle_state.value,
        started_at=execution.started_at,
        completed_at=execution.completed_at,
        cancelled_at=execution.cancelled_at,
        aggregate_revision=execution.aggregate_revision,
        created_at=execution.created_at,
        updated_at=execution.updated_at,
        bindings=tuple(
            TeachingExecutionContentBindingReadModel(
                content_id=binding.content_id,
                content_version_id=binding.content_version_id,
                artifact_kind=binding.artifact_kind,
            )
            for binding in execution.bindings
        ),
        observations=tuple(
            teaching_execution_observation_read_model(item) for item in observations
        ),
    )

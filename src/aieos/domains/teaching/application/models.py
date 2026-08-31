"""Application command/query/result contracts for Teaching Work."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from aieos.domains.teaching.domain.identities import (
    AggregateRevision,
    AssignmentId,
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
    audience_display_label: str | None = None
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

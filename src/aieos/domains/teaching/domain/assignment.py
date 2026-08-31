"""TeachingAssignment aggregate contract.

TeachingAssignment is the Teaching-domain System of Record for teacher-owned
classroom assignment intent. Publication eligibility and ClassRef current
authority are external gates for DEV06-I03 — this factory performs intrinsic
aggregate validation only.

Published != Assigned
Assigned != Externally Delivered / Attempted / Submitted / Graded

class_ref is an opaque School Context identifier. TeachingWork.class_label is
NOT Class identity and is never used here.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import datetime
from typing import Final
from uuid import UUID

from aieos.domains.teaching.domain.assignment_lifecycle import (
    AssignmentLifecycleState,
    parse_lifecycle_state,
)
from aieos.domains.teaching.domain.audience_type import AudienceType, parse_audience_type
from aieos.domains.teaching.domain.errors import InvalidTeachingAssignmentError
from aieos.domains.teaching.domain.identities import (
    AggregateRevision,
    AssignmentId,
    WorkId,
    require_foreign_uuid,
)

MAX_CLASS_REF_LENGTH: Final = 512
MAX_DISPLAY_LABEL_LENGTH: Final = 255


def _require_aware(value: datetime, *, label: str) -> datetime:
    if not isinstance(value, datetime):
        raise InvalidTeachingAssignmentError(f"{label} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise InvalidTeachingAssignmentError(f"{label} must be timezone-aware")
    return value


def _require_text(value: str, *, label: str, max_length: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidTeachingAssignmentError(f"{label} must be a non-empty string")
    stripped = value.strip()
    if len(stripped) > max_length:
        raise InvalidTeachingAssignmentError(
            f"{label} must be at most {max_length} characters"
        )
    return stripped


def _optional_text(value: str | None, *, label: str, max_length: int) -> str | None:
    if value is None:
        return None
    return _require_text(value, label=label, max_length=max_length)


@dataclass(frozen=True, slots=True)
class TeachingAssignment:
    """Durable teacher-owned classroom assignment intent snapshot."""

    assignment_id: AssignmentId
    tenant_id: UUID
    teacher_principal_id: UUID
    content_id: UUID
    content_version_id: UUID
    audience_type: AudienceType
    class_ref: str
    audience_display_label: str | None
    source_work_id: WorkId | None
    lifecycle_state: AssignmentLifecycleState
    assigned_at: datetime
    available_from: datetime
    due_at: datetime | None
    closed_at: datetime | None
    cancelled_at: datetime | None
    aggregate_revision: AggregateRevision
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        set_ = object.__setattr__
        set_(self, "audience_type", parse_audience_type(self.audience_type))
        set_(self, "lifecycle_state", parse_lifecycle_state(self.lifecycle_state))
        set_(
            self,
            "class_ref",
            _require_text(
                self.class_ref, label="class_ref", max_length=MAX_CLASS_REF_LENGTH
            ),
        )
        set_(
            self,
            "audience_display_label",
            _optional_text(
                self.audience_display_label,
                label="audience_display_label",
                max_length=MAX_DISPLAY_LABEL_LENGTH,
            ),
        )
        require_foreign_uuid(self.tenant_id, label="tenant_id")
        require_foreign_uuid(
            self.teacher_principal_id, label="teacher_principal_id"
        )
        require_foreign_uuid(self.content_id, label="content_id")
        require_foreign_uuid(self.content_version_id, label="content_version_id")
        if not isinstance(self.assignment_id, AssignmentId):
            raise InvalidTeachingAssignmentError(
                "assignment_id must be an AssignmentId"
            )
        if self.source_work_id is not None and not isinstance(
            self.source_work_id, WorkId
        ):
            raise InvalidTeachingAssignmentError(
                "source_work_id must be a WorkId when supplied"
            )
        if not isinstance(self.aggregate_revision, AggregateRevision):
            raise InvalidTeachingAssignmentError(
                "aggregate_revision must be an AggregateRevision"
            )
        _require_aware(self.assigned_at, label="assigned_at")
        _require_aware(self.available_from, label="available_from")
        _require_aware(self.created_at, label="created_at")
        _require_aware(self.updated_at, label="updated_at")
        if self.due_at is not None:
            _require_aware(self.due_at, label="due_at")
        if self.closed_at is not None:
            _require_aware(self.closed_at, label="closed_at")
        if self.cancelled_at is not None:
            _require_aware(self.cancelled_at, label="cancelled_at")
        self._assert_lifecycle_timestamps()

    def _assert_lifecycle_timestamps(self) -> None:
        state = self.lifecycle_state
        if state is AssignmentLifecycleState.ACTIVE:
            if self.closed_at is not None or self.cancelled_at is not None:
                raise InvalidTeachingAssignmentError(
                    "ACTIVE assignment must have closed_at and cancelled_at unset"
                )
        elif state is AssignmentLifecycleState.CLOSED:
            if self.closed_at is None or self.cancelled_at is not None:
                raise InvalidTeachingAssignmentError(
                    "CLOSED assignment requires closed_at and unset cancelled_at"
                )
        elif state is AssignmentLifecycleState.CANCELLED:
            if self.cancelled_at is None or self.closed_at is not None:
                raise InvalidTeachingAssignmentError(
                    "CANCELLED assignment requires cancelled_at and unset closed_at"
                )

    @property
    def is_terminal(self) -> bool:
        return self.lifecycle_state in (
            AssignmentLifecycleState.CLOSED,
            AssignmentLifecycleState.CANCELLED,
        )

    @classmethod
    def create(
        cls,
        *,
        tenant_id: UUID,
        teacher_principal_id: UUID,
        content_id: UUID,
        content_version_id: UUID,
        class_ref: str,
        assigned_at: datetime,
        audience_display_label: str | None = None,
        source_work_id: WorkId | None = None,
        available_from: datetime | None = None,
        due_at: datetime | None = None,
        assignment_id: AssignmentId | None = None,
        audience_type: AudienceType | str = AudienceType.CLASS,
    ) -> TeachingAssignment:
        """Materialize a new ACTIVE TeachingAssignment.

        Intrinsic validation only. Does not prove publication eligibility or
        ClassRef current authority (DEV06-I03).
        """
        _require_aware(assigned_at, label="assigned_at")
        effective_available = (
            assigned_at if available_from is None else available_from
        )
        return cls(
            assignment_id=(
                AssignmentId.generate() if assignment_id is None else assignment_id
            ),
            tenant_id=tenant_id,
            teacher_principal_id=teacher_principal_id,
            content_id=content_id,
            content_version_id=content_version_id,
            audience_type=audience_type,
            class_ref=class_ref,
            audience_display_label=audience_display_label,
            source_work_id=source_work_id,
            lifecycle_state=AssignmentLifecycleState.ACTIVE,
            assigned_at=assigned_at,
            available_from=effective_available,
            due_at=due_at,
            closed_at=None,
            cancelled_at=None,
            aggregate_revision=AggregateRevision(0),
            created_at=assigned_at,
            updated_at=assigned_at,
        )

    def update_due_at(
        self,
        *,
        due_at: datetime | None,
        updated_at: datetime,
    ) -> TeachingAssignment:
        if self.lifecycle_state is not AssignmentLifecycleState.ACTIVE:
            raise InvalidTeachingAssignmentError(
                "due_at may only be updated while the assignment is ACTIVE"
            )
        _require_aware(updated_at, label="updated_at")
        if due_at is not None:
            _require_aware(due_at, label="due_at")
        return dataclasses.replace(
            self,
            due_at=due_at,
            aggregate_revision=self.aggregate_revision.next(),
            updated_at=updated_at,
        )

    def close(self, *, closed_at: datetime) -> TeachingAssignment:
        if self.lifecycle_state is not AssignmentLifecycleState.ACTIVE:
            raise InvalidTeachingAssignmentError(
                "only an ACTIVE assignment can be closed"
            )
        _require_aware(closed_at, label="closed_at")
        return dataclasses.replace(
            self,
            lifecycle_state=AssignmentLifecycleState.CLOSED,
            closed_at=closed_at,
            cancelled_at=None,
            aggregate_revision=self.aggregate_revision.next(),
            updated_at=closed_at,
        )

    def cancel(self, *, cancelled_at: datetime) -> TeachingAssignment:
        if self.lifecycle_state is not AssignmentLifecycleState.ACTIVE:
            raise InvalidTeachingAssignmentError(
                "only an ACTIVE assignment can be cancelled"
            )
        _require_aware(cancelled_at, label="cancelled_at")
        return dataclasses.replace(
            self,
            lifecycle_state=AssignmentLifecycleState.CANCELLED,
            cancelled_at=cancelled_at,
            closed_at=None,
            aggregate_revision=self.aggregate_revision.next(),
            updated_at=cancelled_at,
        )

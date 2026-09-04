"""ClassroomAssessment aggregate contract.

ClassroomAssessment is the Assessment-domain System of Record for class-level
assessment evidence. RECORDED means only that the represented human teacher
recorded this class-level judgement — not mastery, learner attempt, or Improve.

teacher_principal_id is the represented / effective HUMAN teacher whose
classroom assessment is being recorded — not the HTTP caller, service
workload, or machine identity by default.

class_ref is an opaque School Context identifier. Current-authority ClassRef
validation belongs to DEV08-I02 — this factory performs intrinsic validation
only.

Optional work_id / execution_id / assignment_id are opaque composition
identities. They are not Teaching aggregate objects and are not authorized
by this domain factory.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import datetime
from typing import Final
from uuid import UUID

from aieos.domains.assessment.domain.errors import InvalidClassroomAssessmentError
from aieos.domains.assessment.domain.identities import (
    AggregateRevision,
    AssessmentId,
    require_foreign_uuid,
    require_optional_foreign_uuid,
)
from aieos.domains.assessment.domain.lifecycle import (
    AssessmentLifecycleState,
    parse_assessment_lifecycle_state,
)
from aieos.domains.assessment.domain.result import (
    ClassResultLevel,
    parse_class_result_level,
)

MAX_CLASS_REF_LENGTH: Final = 512
MAX_CLASS_RESULT_NOTE_LENGTH: Final = 4096


def _require_aware(value: datetime, *, label: str) -> datetime:
    if not isinstance(value, datetime):
        raise InvalidClassroomAssessmentError(f"{label} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise InvalidClassroomAssessmentError(f"{label} must be timezone-aware")
    return value


def _require_text(value: str, *, label: str, max_length: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidClassroomAssessmentError(f"{label} must be a non-empty string")
    stripped = value.strip()
    if len(stripped) > max_length:
        raise InvalidClassroomAssessmentError(
            f"{label} must be at most {max_length} characters"
        )
    return stripped


def _optional_note(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidClassroomAssessmentError("class_result_note must be a string")
    stripped = value.strip()
    if not stripped:
        return None
    if len(stripped) > MAX_CLASS_RESULT_NOTE_LENGTH:
        raise InvalidClassroomAssessmentError(
            f"class_result_note must be at most {MAX_CLASS_RESULT_NOTE_LENGTH} characters"
        )
    return stripped


@dataclass(frozen=True, slots=True)
class ClassroomAssessment:
    """Durable teacher class-level assessment snapshot."""

    assessment_id: AssessmentId
    tenant_id: UUID
    teacher_principal_id: UUID
    class_ref: str
    content_id: UUID
    content_version_id: UUID
    class_result_level: ClassResultLevel
    class_result_note: str | None
    lifecycle_state: AssessmentLifecycleState
    work_id: UUID | None
    execution_id: UUID | None
    assignment_id: UUID | None
    aggregate_revision: AggregateRevision
    recorded_at: datetime
    voided_at: datetime | None
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        set_ = object.__setattr__
        set_(
            self,
            "lifecycle_state",
            parse_assessment_lifecycle_state(self.lifecycle_state),
        )
        set_(
            self,
            "class_result_level",
            parse_class_result_level(self.class_result_level),
        )
        set_(
            self,
            "class_ref",
            _require_text(
                self.class_ref, label="class_ref", max_length=MAX_CLASS_REF_LENGTH
            ),
        )
        set_(self, "class_result_note", _optional_note(self.class_result_note))
        require_foreign_uuid(self.tenant_id, label="tenant_id")
        require_foreign_uuid(
            self.teacher_principal_id, label="teacher_principal_id"
        )
        require_foreign_uuid(self.content_id, label="content_id")
        require_foreign_uuid(self.content_version_id, label="content_version_id")
        set_(
            self,
            "work_id",
            require_optional_foreign_uuid(self.work_id, label="work_id"),
        )
        set_(
            self,
            "execution_id",
            require_optional_foreign_uuid(self.execution_id, label="execution_id"),
        )
        set_(
            self,
            "assignment_id",
            require_optional_foreign_uuid(self.assignment_id, label="assignment_id"),
        )
        if not isinstance(self.assessment_id, AssessmentId):
            raise InvalidClassroomAssessmentError(
                "assessment_id must be an AssessmentId"
            )
        if not isinstance(self.aggregate_revision, AggregateRevision):
            raise InvalidClassroomAssessmentError(
                "aggregate_revision must be an AggregateRevision"
            )
        _require_aware(self.recorded_at, label="recorded_at")
        _require_aware(self.created_at, label="created_at")
        _require_aware(self.updated_at, label="updated_at")
        if self.voided_at is not None:
            _require_aware(self.voided_at, label="voided_at")
        if self.updated_at < self.created_at:
            raise InvalidClassroomAssessmentError(
                "updated_at must be greater than or equal to created_at"
            )
        self._assert_lifecycle_timestamps()

    def _assert_lifecycle_timestamps(self) -> None:
        state = self.lifecycle_state
        if state is AssessmentLifecycleState.RECORDED:
            if self.voided_at is not None:
                raise InvalidClassroomAssessmentError(
                    "RECORDED assessment must have voided_at unset"
                )
        elif state is AssessmentLifecycleState.VOIDED:
            if self.voided_at is None:
                raise InvalidClassroomAssessmentError(
                    "VOIDED assessment requires voided_at"
                )

    @property
    def is_terminal(self) -> bool:
        return self.lifecycle_state is AssessmentLifecycleState.VOIDED

    @classmethod
    def record(
        cls,
        *,
        tenant_id: UUID,
        teacher_principal_id: UUID,
        class_ref: str,
        content_id: UUID,
        content_version_id: UUID,
        class_result_level: ClassResultLevel | str,
        recorded_at: datetime,
        class_result_note: str | None = None,
        work_id: UUID | None = None,
        execution_id: UUID | None = None,
        assignment_id: UUID | None = None,
        assessment_id: AssessmentId | None = None,
    ) -> ClassroomAssessment:
        """Materialize a new RECORDED ClassroomAssessment.

        Intrinsic validation only. Does not prove ClassRef current authority,
        Content publication eligibility, or Teaching composition (DEV08-I02).
        """
        _require_aware(recorded_at, label="recorded_at")
        aid = AssessmentId.generate() if assessment_id is None else assessment_id
        return cls(
            assessment_id=aid,
            tenant_id=tenant_id,
            teacher_principal_id=teacher_principal_id,
            class_ref=class_ref,
            content_id=content_id,
            content_version_id=content_version_id,
            class_result_level=parse_class_result_level(class_result_level),
            class_result_note=class_result_note,
            lifecycle_state=AssessmentLifecycleState.RECORDED,
            work_id=work_id,
            execution_id=execution_id,
            assignment_id=assignment_id,
            aggregate_revision=AggregateRevision(0),
            recorded_at=recorded_at,
            voided_at=None,
            created_at=recorded_at,
            updated_at=recorded_at,
        )

    def correct(
        self,
        *,
        class_result_level: ClassResultLevel | str,
        class_result_note: str | None,
        updated_at: datetime,
    ) -> ClassroomAssessment:
        if self.lifecycle_state is not AssessmentLifecycleState.RECORDED:
            raise InvalidClassroomAssessmentError(
                "only a RECORDED assessment can be corrected"
            )
        _require_aware(updated_at, label="updated_at")
        return dataclasses.replace(
            self,
            class_result_level=parse_class_result_level(class_result_level),
            class_result_note=class_result_note,
            aggregate_revision=self.aggregate_revision.next(),
            updated_at=updated_at,
        )

    def void(self, *, voided_at: datetime) -> ClassroomAssessment:
        if self.lifecycle_state is not AssessmentLifecycleState.RECORDED:
            raise InvalidClassroomAssessmentError(
                "only a RECORDED assessment can be voided"
            )
        _require_aware(voided_at, label="voided_at")
        return dataclasses.replace(
            self,
            lifecycle_state=AssessmentLifecycleState.VOIDED,
            voided_at=voided_at,
            aggregate_revision=self.aggregate_revision.next(),
            updated_at=voided_at,
        )

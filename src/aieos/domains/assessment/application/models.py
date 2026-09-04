"""Assessment application command and read models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from aieos.domains.assessment.domain.classroom_assessment import ClassroomAssessment
from aieos.domains.assessment.domain.lifecycle import AssessmentLifecycleState
from aieos.domains.assessment.domain.result import ClassResultLevel


@dataclass(frozen=True, slots=True)
class RecordClassroomAssessmentCommand:
    class_ref: str
    content_id: UUID
    content_version_id: UUID
    class_result_level: ClassResultLevel | str
    class_result_note: str | None = None
    work_id: UUID | None = None
    execution_id: UUID | None = None
    assignment_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class CorrectClassroomAssessmentCommand:
    class_result_level: ClassResultLevel | str
    class_result_note: str | None = None


@dataclass(frozen=True, slots=True)
class ListClassroomAssessmentsQuery:
    class_ref: str | None = None
    work_id: UUID | None = None
    execution_id: UUID | None = None
    assignment_id: UUID | None = None
    lifecycle_state: AssessmentLifecycleState | str | None = None
    limit: int = 50


@dataclass(frozen=True, slots=True)
class ClassroomAssessmentReadModel:
    assessment_id: UUID
    teacher_principal_id: UUID
    class_ref: str
    content_id: UUID
    content_version_id: UUID
    class_result_level: str
    class_result_note: str | None
    lifecycle_state: str
    work_id: UUID | None
    execution_id: UUID | None
    assignment_id: UUID | None
    aggregate_revision: int
    recorded_at: datetime
    voided_at: datetime | None
    created_at: datetime
    updated_at: datetime


def classroom_assessment_read_model(
    assessment: ClassroomAssessment,
) -> ClassroomAssessmentReadModel:
    return ClassroomAssessmentReadModel(
        assessment_id=assessment.assessment_id.value,
        teacher_principal_id=assessment.teacher_principal_id,
        class_ref=assessment.class_ref,
        content_id=assessment.content_id,
        content_version_id=assessment.content_version_id,
        class_result_level=assessment.class_result_level.value,
        class_result_note=assessment.class_result_note,
        lifecycle_state=assessment.lifecycle_state.value,
        work_id=assessment.work_id,
        execution_id=assessment.execution_id,
        assignment_id=assessment.assignment_id,
        aggregate_revision=int(assessment.aggregate_revision),
        recorded_at=assessment.recorded_at,
        voided_at=assessment.voided_at,
        created_at=assessment.created_at,
        updated_at=assessment.updated_at,
    )

"""Assessment-eligible content kinds and Case A/B/C composition orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final
from uuid import UUID

from aieos.domains.assessment.application.errors import CompositionConflict
from aieos.domains.assessment.application.ports import AssessmentUnitOfWork
from aieos.domains.education.schema import (
    HOMEWORK_CONTENT_TYPE,
    QUIZ_CONTENT_TYPE,
    WORKSHEET_CONTENT_TYPE,
)

ASSESSMENT_ELIGIBLE_CONTENT_TYPES: Final[frozenset[str]] = frozenset(
    {
        QUIZ_CONTENT_TYPE,
        WORKSHEET_CONTENT_TYPE,
        HOMEWORK_CONTENT_TYPE,
    }
)


@dataclass(frozen=True, slots=True)
class CompositionRequest:
    class_ref: str
    content_id: UUID
    content_version_id: UUID
    work_id: UUID | None
    execution_id: UUID | None
    assignment_id: UUID | None


def validate_composition(
    uow: AssessmentUnitOfWork,
    *,
    teacher_principal_id: UUID,
    request: CompositionRequest,
) -> None:
    """Deterministic Cases A/B/C. Never silently downgrade failed A/B to C."""
    execution_work_id: UUID | None = None
    assignment_source_work_id: UUID | None = None

    if request.execution_id is not None:
        execution_work_id = uow.teaching_composition.load_completed_execution(
            execution_id=request.execution_id,
            teacher_principal_id=teacher_principal_id,
            class_ref=request.class_ref,
            content_id=request.content_id,
            content_version_id=request.content_version_id,
            work_id=request.work_id,
        )

    if request.assignment_id is not None:
        assignment_source_work_id = uow.teaching_composition.load_class_assignment(
            assignment_id=request.assignment_id,
            teacher_principal_id=teacher_principal_id,
            class_ref=request.class_ref,
            content_id=request.content_id,
            content_version_id=request.content_version_id,
            work_id=request.work_id,
        )

    if request.execution_id is not None and request.assignment_id is not None:
        if (
            request.work_id is not None
            and execution_work_id is not None
            and assignment_source_work_id is not None
            and (
                execution_work_id != assignment_source_work_id
                or request.work_id != execution_work_id
            )
        ):
            raise CompositionConflict(
                "execution and assignment composition facts do not agree"
            )
        if (
            execution_work_id is not None
            and assignment_source_work_id is not None
            and execution_work_id != assignment_source_work_id
        ):
            raise CompositionConflict(
                "execution and assignment source work identities disagree"
            )

    if request.execution_id is None and request.assignment_id is None:
        uow.content_authority.verify_current_published_assessment_content(
            content_id=request.content_id,
            content_version_id=request.content_version_id,
        )

    if request.work_id is not None and request.execution_id is None:
        # Optional work without execution: ownership check (assignment path may
        # already have matched source_work_id; still require owned work).
        uow.teaching_composition.require_owned_work(
            work_id=request.work_id,
            teacher_principal_id=teacher_principal_id,
        )

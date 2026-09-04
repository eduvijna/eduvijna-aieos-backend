"""ClassroomAssessment lifecycle states.

Frozen by ADR-AIEOS-055: RECORDED / VOIDED only.
Rejected: IN_PROGRESS, COMPLETED, CANCELLED, ASSESSED, GRADED, MASTERED.
"""

from __future__ import annotations

from enum import StrEnum

from aieos.domains.assessment.domain.errors import InvalidClassroomAssessmentError


class AssessmentLifecycleState(StrEnum):
    RECORDED = "RECORDED"
    VOIDED = "VOIDED"


def parse_assessment_lifecycle_state(
    value: AssessmentLifecycleState | str,
) -> AssessmentLifecycleState:
    if isinstance(value, AssessmentLifecycleState):
        return value
    if not isinstance(value, str):
        raise InvalidClassroomAssessmentError("lifecycle_state must be a string")
    try:
        return AssessmentLifecycleState(value)
    except ValueError as exc:
        raise InvalidClassroomAssessmentError(
            f"lifecycle_state is not a frozen ClassroomAssessment state: {value!r}"
        ) from exc

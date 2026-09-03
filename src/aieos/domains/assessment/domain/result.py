"""ClassroomAssessment class-level result contract.

Frozen by ADR-AIEOS-055: DEMONSTRATED / MIXED / NOT_YET_DEMONSTRATED only.
Rejected: NEEDS_RETEACH, CLASS_NEEDS_RETEACH, MASTERED, PASSED, FAILED,
GRADE, SCORE.

The result is the represented teacher's governed class-level judgement for
the exact assessment context. It is not Mastery.
"""

from __future__ import annotations

from enum import StrEnum

from aieos.domains.assessment.domain.errors import InvalidClassroomAssessmentError


class ClassResultLevel(StrEnum):
    DEMONSTRATED = "DEMONSTRATED"
    MIXED = "MIXED"
    NOT_YET_DEMONSTRATED = "NOT_YET_DEMONSTRATED"


def parse_class_result_level(
    value: ClassResultLevel | str,
) -> ClassResultLevel:
    if isinstance(value, ClassResultLevel):
        return value
    if not isinstance(value, str):
        raise InvalidClassroomAssessmentError("class_result_level must be a string")
    try:
        return ClassResultLevel(value)
    except ValueError as exc:
        raise InvalidClassroomAssessmentError(
            f"class_result_level is not a frozen ClassroomAssessment result: {value!r}"
        ) from exc

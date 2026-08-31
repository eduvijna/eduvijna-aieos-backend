"""TeachingAssignment audience types.

Baseline audience is class-scoped via opaque ClassRef. No learner subset,
individual learner, or roster-member audience types are authorized.
"""

from __future__ import annotations

from enum import StrEnum

from aieos.domains.teaching.domain.errors import InvalidTeachingAssignmentError


class AudienceType(StrEnum):
    CLASS = "class"


def parse_audience_type(value: AudienceType | str) -> AudienceType:
    if isinstance(value, AudienceType):
        return value
    if not isinstance(value, str):
        raise InvalidTeachingAssignmentError("audience_type must be a string")
    try:
        return AudienceType(value)
    except ValueError as exc:
        raise InvalidTeachingAssignmentError(
            f"audience_type is not a supported TeachingAssignment audience: {value!r}"
        ) from exc

"""TeachingAssignment lifecycle states.

Frozen by ADR-AIEOS-053: ACTIVE / CLOSED / CANCELLED only.
No DRAFT, SCHEDULED, COMPLETED, DELIVERED, SUBMITTED, or GRADED.
"""

from __future__ import annotations

from enum import StrEnum

from aieos.domains.teaching.domain.errors import InvalidTeachingAssignmentError


class AssignmentLifecycleState(StrEnum):
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


def parse_lifecycle_state(
    value: AssignmentLifecycleState | str,
) -> AssignmentLifecycleState:
    if isinstance(value, AssignmentLifecycleState):
        return value
    if not isinstance(value, str):
        raise InvalidTeachingAssignmentError("lifecycle_state must be a string")
    try:
        return AssignmentLifecycleState(value)
    except ValueError as exc:
        raise InvalidTeachingAssignmentError(
            f"lifecycle_state is not a frozen TeachingAssignment state: {value!r}"
        ) from exc

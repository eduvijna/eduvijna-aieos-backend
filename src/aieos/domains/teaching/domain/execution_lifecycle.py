"""TeachingExecution lifecycle states.

Frozen by ADR-AIEOS-054: IN_PROGRESS / COMPLETED / CANCELLED only.
Rejected: PLANNED, SCHEDULED, DELIVERED, ASSESSED, GRADED, MASTERED.
Assigned is TeachingAssignment authority — not a TeachingExecution state.
"""

from __future__ import annotations

from enum import StrEnum

from aieos.domains.teaching.domain.errors import InvalidTeachingExecutionError


class ExecutionLifecycleState(StrEnum):
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


def parse_execution_lifecycle_state(
    value: ExecutionLifecycleState | str,
) -> ExecutionLifecycleState:
    if isinstance(value, ExecutionLifecycleState):
        return value
    if not isinstance(value, str):
        raise InvalidTeachingExecutionError("lifecycle_state must be a string")
    try:
        return ExecutionLifecycleState(value)
    except ValueError as exc:
        raise InvalidTeachingExecutionError(
            f"lifecycle_state is not a frozen TeachingExecution state: {value!r}"
        ) from exc

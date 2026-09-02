"""TeachingExecutionObservation kinds.

Frozen by ADR-AIEOS-054: PRIVATE_EXECUTION_NOTE / CLASS_OBSERVATION only.
No learner-specific observation kinds.
"""

from __future__ import annotations

from enum import StrEnum

from aieos.domains.teaching.domain.errors import (
    InvalidTeachingExecutionObservationError,
)


class ObservationKind(StrEnum):
    PRIVATE_EXECUTION_NOTE = "PRIVATE_EXECUTION_NOTE"
    CLASS_OBSERVATION = "CLASS_OBSERVATION"


def parse_observation_kind(value: ObservationKind | str) -> ObservationKind:
    if isinstance(value, ObservationKind):
        return value
    if not isinstance(value, str):
        raise InvalidTeachingExecutionObservationError(
            "observation_kind must be a string"
        )
    try:
        return ObservationKind(value)
    except ValueError as exc:
        raise InvalidTeachingExecutionObservationError(
            f"observation_kind is not a frozen TeachingExecutionObservation "
            f"kind: {value!r}"
        ) from exc

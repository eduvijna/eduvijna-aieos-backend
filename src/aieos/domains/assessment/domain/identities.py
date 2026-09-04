"""Assessment identity value objects.

Assessment-owned semantic IDs only. Shared platform identities (tenant,
principal) and opaque composition references are stdlib UUID field values.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from uuid import UUID

from aieos.domains.assessment.domain.errors import (
    InvalidAggregateRevisionError,
    InvalidAssessmentIdentityError,
)


def _require_uuid7(value: UUID | str, *, label: str) -> UUID:
    parsed: UUID
    if isinstance(value, UUID):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = UUID(value)
        except ValueError as exc:
            raise InvalidAssessmentIdentityError(f"{label} is not a valid UUID") from exc
    else:
        raise InvalidAssessmentIdentityError(f"{label} must be a UUID")
    if parsed.version != 7:
        raise InvalidAssessmentIdentityError(
            f"{label} must be UUIDv7; got version {parsed.version!r}"
        )
    return parsed


def require_foreign_uuid(value: UUID, *, label: str) -> UUID:
    """Accept a stdlib UUID for a non-Assessment-owned identity field."""
    if not isinstance(value, UUID):
        raise InvalidAssessmentIdentityError(f"{label} must be a UUID")
    return value


def require_optional_foreign_uuid(value: UUID | None, *, label: str) -> UUID | None:
    if value is None:
        return None
    return require_foreign_uuid(value, label=label)


@dataclass(frozen=True, slots=True)
class AssessmentId:
    """Stable ClassroomAssessment identity."""

    value: UUID

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "value", _require_uuid7(self.value, label="assessment_id")
        )

    @classmethod
    def generate(cls) -> AssessmentId:
        return cls(uuid.uuid7())

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True, slots=True)
class AggregateRevision:
    """Optimistic concurrency revision of an Assessment-owned aggregate."""

    value: int

    def __post_init__(self) -> None:
        if (
            isinstance(self.value, bool)
            or not isinstance(self.value, int)
            or self.value < 0
        ):
            raise InvalidAggregateRevisionError(
                "aggregate_revision must be a non-negative integer"
            )

    def next(self) -> AggregateRevision:
        return AggregateRevision(self.value + 1)

    def __int__(self) -> int:
        return self.value

"""Teaching identity value objects.

Teaching-owned semantic IDs only. Shared platform identities (tenant,
principal, correlation) are referenced as stdlib UUID field values, mirroring
the Generic Content pattern.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from uuid import UUID

from aieos.domains.teaching.domain.errors import (
    InvalidAggregateRevisionError,
    InvalidTeachingIdentityError,
)


def _require_uuid7(value: UUID | str, *, label: str) -> UUID:
    parsed: UUID
    if isinstance(value, UUID):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = UUID(value)
        except ValueError as exc:
            raise InvalidTeachingIdentityError(f"{label} is not a valid UUID") from exc
    else:
        raise InvalidTeachingIdentityError(f"{label} must be a UUID")
    if parsed.version != 7:
        raise InvalidTeachingIdentityError(
            f"{label} must be UUIDv7; got version {parsed.version!r}"
        )
    return parsed


def require_foreign_uuid(value: UUID, *, label: str) -> UUID:
    """Accept a stdlib UUID for a non-Teaching-owned identity field."""
    if not isinstance(value, UUID):
        raise InvalidTeachingIdentityError(f"{label} must be a UUID")
    return value


@dataclass(frozen=True, slots=True)
class WorkId:
    """Stable Teaching Work identity for the whole preparation lifecycle."""

    value: UUID

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _require_uuid7(self.value, label="work_id"))

    @classmethod
    def generate(cls) -> WorkId:
        return cls(uuid.uuid7())

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True, slots=True)
class AggregateRevision:
    """Optimistic concurrency revision of the TeachingWork aggregate."""

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

"""Generic Content identity value objects.

Content-owned semantic IDs only. Shared platform identities (tenant, principal,
correlation, delegation) are not defined here; they are referenced as stdlib
UUID field values until an approved shared platform contract exists.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from uuid import UUID

from aieos.domains.content.domain.errors import (
    InvalidAggregateRevisionError,
    InvalidContentIdentityError,
    InvalidVersionNumberError,
)


def _require_uuid7(value: UUID | str, *, label: str) -> UUID:
    parsed: UUID
    if isinstance(value, UUID):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = UUID(value)
        except ValueError as exc:
            raise InvalidContentIdentityError(f"{label} is not a valid UUID") from exc
    else:
        raise InvalidContentIdentityError(f"{label} must be a UUID")
    if parsed.version != 7:
        raise InvalidContentIdentityError(
            f"{label} must be UUIDv7; got version {parsed.version!r}"
        )
    return parsed


def require_foreign_uuid(value: UUID, *, label: str) -> UUID:
    """Accept a stdlib UUID for a non-Content-owned identity field.

    Does not wrap tenant/principal/correlation/delegation as Content types.
    """
    if not isinstance(value, UUID):
        raise InvalidContentIdentityError(f"{label} must be a UUID")
    return value


@dataclass(frozen=True, slots=True)
class ContentId:
    """Stable Content identity across every version/review/publication lifecycle."""

    value: UUID

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _require_uuid7(self.value, label="content_id"))

    @classmethod
    def generate(cls) -> ContentId:
        return cls(uuid.uuid7())

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True, slots=True)
class ContentVersionId:
    """Identity of one immutable ContentVersion. Never equal to ContentId."""

    value: UUID

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _require_uuid7(self.value, label="version_id"))

    @classmethod
    def generate(cls) -> ContentVersionId:
        return cls(uuid.uuid7())

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True, slots=True)
class ReviewDecisionId:
    value: UUID

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "value", _require_uuid7(self.value, label="review_decision_id")
        )

    @classmethod
    def generate(cls) -> ReviewDecisionId:
        return cls(uuid.uuid7())

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True, slots=True)
class PublicationId:
    value: UUID

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "value", _require_uuid7(self.value, label="publication_id")
        )

    @classmethod
    def generate(cls) -> PublicationId:
        return cls(uuid.uuid7())

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True, slots=True)
class VersionNumber:
    """Business version of a ContentVersion. Distinct from aggregate_revision."""

    value: int

    def __post_init__(self) -> None:
        if isinstance(self.value, bool) or not isinstance(self.value, int) or self.value < 1:
            raise InvalidVersionNumberError(
                "version_number must be a positive integer"
            )

    def next(self) -> VersionNumber:
        return VersionNumber(self.value + 1)

    def __int__(self) -> int:
        return self.value


@dataclass(frozen=True, slots=True)
class AggregateRevision:
    """Optimistic/concurrency revision of the Content aggregate.

    Distinct from VersionNumber. Not a business version.
    """

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

    def __int__(self) -> int:
        return self.value

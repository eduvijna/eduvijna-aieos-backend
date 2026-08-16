"""Asset-owned identity value objects (ADR-AIEOS-033 / PED-I10B1).

AssetRevisionNumber is the business ResourceRef.resource_revision.
AssetAggregateRevision is optimistic-concurrency authority only.
They are distinct types and must never be substituted for each other.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from uuid import UUID

from aieos.domains.asset.domain.errors import (
    InvalidAssetAggregateRevisionError,
    InvalidAssetIdentityError,
    InvalidAssetRevisionNumberError,
)


def _require_uuid7(value: UUID | str, *, label: str) -> UUID:
    parsed: UUID
    if isinstance(value, UUID):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = UUID(value)
        except ValueError as exc:
            raise InvalidAssetIdentityError(f"{label} is not a valid UUID") from exc
    else:
        raise InvalidAssetIdentityError(f"{label} must be a UUID")
    if parsed.version != 7:
        raise InvalidAssetIdentityError(
            f"{label} must be UUIDv7; got version {parsed.version!r}"
        )
    return parsed


def require_foreign_uuid(value: UUID, *, label: str) -> UUID:
    """Accept a stdlib UUID for a non-Asset-owned identity field.

    tenant_id / principal_id remain platform foreign UUIDs (not Asset wrappers).
    """
    if not isinstance(value, UUID):
        raise InvalidAssetIdentityError(f"{label} must be a UUID")
    return value


@dataclass(frozen=True, slots=True)
class AssetId:
    """Stable Asset identity across every byte revision and lifecycle change."""

    value: UUID

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _require_uuid7(self.value, label="asset_id"))

    @classmethod
    def generate(cls) -> AssetId:
        return cls(uuid.uuid7())

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True, slots=True)
class AssetRevisionId:
    """Identity of one immutable AssetRevision. Never equal to AssetId."""

    value: UUID

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "value", _require_uuid7(self.value, label="asset_revision_id")
        )

    @classmethod
    def generate(cls) -> AssetRevisionId:
        return cls(uuid.uuid7())

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True, slots=True)
class AssetRevisionNumber:
    """Business resource revision of an AssetRevision.

    Maps to ResourceRef.resource_revision. Distinct from AssetAggregateRevision.
    """

    value: int

    def __post_init__(self) -> None:
        if (
            isinstance(self.value, bool)
            or not isinstance(self.value, int)
            or self.value < 1
        ):
            raise InvalidAssetRevisionNumberError(
                "revision_number must be a positive integer"
            )

    def next(self) -> AssetRevisionNumber:
        return AssetRevisionNumber(self.value + 1)

    def __int__(self) -> int:
        return self.value


@dataclass(frozen=True, slots=True)
class AssetAggregateRevision:
    """Optimistic-concurrency / current-authority revision of the Asset aggregate.

    Distinct from AssetRevisionNumber. Not a business ResourceRef revision.
    """

    value: int

    def __post_init__(self) -> None:
        if (
            isinstance(self.value, bool)
            or not isinstance(self.value, int)
            or self.value < 0
        ):
            raise InvalidAssetAggregateRevisionError(
                "aggregate_revision must be a non-negative integer"
            )

    def __int__(self) -> int:
        return self.value

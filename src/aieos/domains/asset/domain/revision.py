"""Immutable AssetRevision and current AssetRevisionState (ADR-AIEOS-033).

AssetRevision holds immutable byte-bearing business facts.
AssetRevisionState holds mutable governance facts ABOUT a revision.
Physical existence/integrity is evaluated later via BlobStore — not as
blob_exists/available/usable fields here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from aieos.domains.asset.domain.errors import (
    InvalidAssetRevisionError,
    InvalidAssetRevisionStateError,
)
from aieos.domains.asset.domain.identities import (
    AssetId,
    AssetRevisionId,
    AssetRevisionNumber,
    require_foreign_uuid,
)
from aieos.domains.asset.domain.resource_type import (
    AssetResourceType,
    parse_asset_resource_type,
)
from aieos.domains.asset.domain.state import (
    AssetRevisionSafetyState,
    parse_asset_revision_safety_state,
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _require_aware(value: datetime, *, label: str, error: type[Exception]) -> datetime:
    if not isinstance(value, datetime):
        raise error(f"{label} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise error(f"{label} must be timezone-aware")
    return value


def _require_nonempty_str(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidAssetRevisionError(f"{label} must be a non-empty string")
    return value.strip()


def _require_sha256(value: object) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise InvalidAssetRevisionError(
            "sha256 must be exactly 64 lowercase hexadecimal characters"
        )
    return value


@dataclass(frozen=True, slots=True)
class AssetRevision:
    """One immutable byte-bearing business revision of an Asset."""

    tenant_id: UUID
    asset_id: AssetId
    asset_revision_id: AssetRevisionId
    revision_number: AssetRevisionNumber
    resource_type: AssetResourceType
    storage_key: str
    media_type: str
    byte_size: int
    sha256: str
    created_at: datetime
    created_by_principal_id: UUID

    def __post_init__(self) -> None:
        require_foreign_uuid(self.tenant_id, label="tenant_id")
        require_foreign_uuid(
            self.created_by_principal_id, label="created_by_principal_id"
        )
        if not isinstance(self.asset_id, AssetId):
            raise InvalidAssetRevisionError("asset_id must be an AssetId")
        if not isinstance(self.asset_revision_id, AssetRevisionId):
            raise InvalidAssetRevisionError(
                "asset_revision_id must be an AssetRevisionId"
            )
        if not isinstance(self.revision_number, AssetRevisionNumber):
            raise InvalidAssetRevisionError(
                "revision_number must be an AssetRevisionNumber"
            )
        object.__setattr__(
            self, "resource_type", parse_asset_resource_type(self.resource_type)
        )
        object.__setattr__(
            self, "storage_key", _require_nonempty_str(self.storage_key, label="storage_key")
        )
        object.__setattr__(
            self, "media_type", _require_nonempty_str(self.media_type, label="media_type")
        )
        if isinstance(self.byte_size, bool) or not isinstance(self.byte_size, int):
            raise InvalidAssetRevisionError("byte_size must be an integer >= 0")
        if self.byte_size < 0:
            raise InvalidAssetRevisionError("byte_size must be an integer >= 0")
        object.__setattr__(self, "sha256", _require_sha256(self.sha256))
        _require_aware(
            self.created_at, label="created_at", error=InvalidAssetRevisionError
        )


@dataclass(frozen=True, slots=True)
class AssetRevisionState:
    """Immutable snapshot of current mutable governance facts about a revision."""

    tenant_id: UUID
    asset_id: AssetId
    asset_revision_id: AssetRevisionId
    revision_number: AssetRevisionNumber
    safety_state: AssetRevisionSafetyState
    bytes_purged: bool
    updated_at: datetime

    def __post_init__(self) -> None:
        require_foreign_uuid(self.tenant_id, label="tenant_id")
        if not isinstance(self.asset_id, AssetId):
            raise InvalidAssetRevisionStateError("asset_id must be an AssetId")
        if not isinstance(self.asset_revision_id, AssetRevisionId):
            raise InvalidAssetRevisionStateError(
                "asset_revision_id must be an AssetRevisionId"
            )
        if not isinstance(self.revision_number, AssetRevisionNumber):
            raise InvalidAssetRevisionStateError(
                "revision_number must be an AssetRevisionNumber"
            )
        object.__setattr__(
            self,
            "safety_state",
            parse_asset_revision_safety_state(self.safety_state),
        )
        if not isinstance(self.bytes_purged, bool):
            raise InvalidAssetRevisionStateError("bytes_purged must be a boolean")
        _require_aware(
            self.updated_at, label="updated_at", error=InvalidAssetRevisionStateError
        )

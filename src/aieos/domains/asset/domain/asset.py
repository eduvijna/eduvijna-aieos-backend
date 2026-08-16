"""Asset aggregate domain snapshot (ADR-AIEOS-033 / PED-I10B1).

No storage path/URL/provider fields. No Content IDs. No authorization claims.
ACTIVE may legally have current_revision=None. WITHDRAWN/DELETED may retain
current_revision. Usability is not a persisted lifecycle enum.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from aieos.domains.asset.domain.errors import InvalidAssetError
from aieos.domains.asset.domain.identities import (
    AssetAggregateRevision,
    AssetId,
    AssetRevisionNumber,
    require_foreign_uuid,
)
from aieos.domains.asset.domain.resource_type import (
    AssetResourceType,
    parse_asset_resource_type,
)
from aieos.domains.asset.domain.state import (
    AssetLifecycle,
    AssetQuarantineState,
    parse_asset_lifecycle,
    parse_asset_quarantine_state,
)


def _require_aware(value: datetime, *, label: str) -> datetime:
    if not isinstance(value, datetime):
        raise InvalidAssetError(f"{label} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise InvalidAssetError(f"{label} must be timezone-aware")
    return value


@dataclass(frozen=True, slots=True)
class Asset:
    """Immutable authoritative Asset aggregate snapshot."""

    tenant_id: UUID
    asset_id: AssetId
    resource_type: AssetResourceType
    lifecycle: AssetLifecycle
    quarantine_state: AssetQuarantineState
    current_revision: AssetRevisionNumber | None
    aggregate_revision: AssetAggregateRevision
    created_at: datetime
    created_by_principal_id: UUID

    def __post_init__(self) -> None:
        require_foreign_uuid(self.tenant_id, label="tenant_id")
        require_foreign_uuid(
            self.created_by_principal_id, label="created_by_principal_id"
        )
        if not isinstance(self.asset_id, AssetId):
            raise InvalidAssetError("asset_id must be an AssetId")
        object.__setattr__(
            self, "resource_type", parse_asset_resource_type(self.resource_type)
        )
        object.__setattr__(self, "lifecycle", parse_asset_lifecycle(self.lifecycle))
        object.__setattr__(
            self,
            "quarantine_state",
            parse_asset_quarantine_state(self.quarantine_state),
        )
        if self.current_revision is not None and not isinstance(
            self.current_revision, AssetRevisionNumber
        ):
            raise InvalidAssetError(
                "current_revision must be None or an AssetRevisionNumber"
            )
        if not isinstance(self.aggregate_revision, AssetAggregateRevision):
            raise InvalidAssetError(
                "aggregate_revision must be an AssetAggregateRevision"
            )
        _require_aware(self.created_at, label="created_at")

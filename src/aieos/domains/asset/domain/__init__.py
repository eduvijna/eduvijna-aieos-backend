"""Asset pure-domain contracts (PED-I10B1 / ADR-AIEOS-033)."""

from aieos.domains.asset.domain.asset import Asset
from aieos.domains.asset.domain.errors import (
    AssetDomainError,
    InvalidAssetAggregateRevisionError,
    InvalidAssetError,
    InvalidAssetIdentityError,
    InvalidAssetResourceTypeError,
    InvalidAssetRevisionError,
    InvalidAssetRevisionNumberError,
    InvalidAssetRevisionStateError,
    InvalidAssetStateError,
)
from aieos.domains.asset.domain.identities import (
    AssetAggregateRevision,
    AssetId,
    AssetRevisionId,
    AssetRevisionNumber,
    require_foreign_uuid,
)
from aieos.domains.asset.domain.resource_type import (
    ASSET_RESOURCE_TYPES_V1,
    AssetResourceType,
    parse_asset_resource_type,
)
from aieos.domains.asset.domain.revision import AssetRevision, AssetRevisionState
from aieos.domains.asset.domain.state import (
    FROZEN_ASSET_LIFECYCLES,
    FROZEN_ASSET_QUARANTINE_STATES,
    FROZEN_ASSET_REVISION_SAFETY_STATES,
    AssetLifecycle,
    AssetQuarantineState,
    AssetRevisionSafetyState,
    parse_asset_lifecycle,
    parse_asset_quarantine_state,
    parse_asset_revision_safety_state,
)

__all__ = [
    "ASSET_RESOURCE_TYPES_V1",
    "FROZEN_ASSET_LIFECYCLES",
    "FROZEN_ASSET_QUARANTINE_STATES",
    "FROZEN_ASSET_REVISION_SAFETY_STATES",
    "Asset",
    "AssetAggregateRevision",
    "AssetDomainError",
    "AssetId",
    "AssetLifecycle",
    "AssetQuarantineState",
    "AssetResourceType",
    "AssetRevision",
    "AssetRevisionId",
    "AssetRevisionNumber",
    "AssetRevisionSafetyState",
    "AssetRevisionState",
    "InvalidAssetAggregateRevisionError",
    "InvalidAssetError",
    "InvalidAssetIdentityError",
    "InvalidAssetResourceTypeError",
    "InvalidAssetRevisionError",
    "InvalidAssetRevisionNumberError",
    "InvalidAssetRevisionStateError",
    "InvalidAssetStateError",
    "parse_asset_lifecycle",
    "parse_asset_quarantine_state",
    "parse_asset_resource_type",
    "parse_asset_revision_safety_state",
    "require_foreign_uuid",
]

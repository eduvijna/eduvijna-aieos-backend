"""Exact V1 Asset resource-type vocabulary (ADR-AIEOS-033 / PED-I10B1).

No wildcards, prefix matching, case-insensitive expansion, or unknown types.
"""

from __future__ import annotations

from enum import StrEnum

from aieos.domains.asset.domain.errors import InvalidAssetResourceTypeError


class AssetResourceType(StrEnum):
    IMAGE = "asset.image"
    DOCUMENT = "asset.document"
    AUDIO = "asset.audio"
    VIDEO = "asset.video"


ASSET_RESOURCE_TYPES_V1: frozenset[str] = frozenset(
    member.value for member in AssetResourceType
)


def parse_asset_resource_type(value: str | AssetResourceType) -> AssetResourceType:
    if isinstance(value, AssetResourceType):
        return value
    if not isinstance(value, str):
        raise InvalidAssetResourceTypeError(
            f"unknown asset resource type {value!r}; "
            f"allowed={sorted(ASSET_RESOURCE_TYPES_V1)}"
        )
    try:
        return AssetResourceType(value)
    except ValueError as exc:
        raise InvalidAssetResourceTypeError(
            f"unknown asset resource type {value!r}; "
            f"allowed={sorted(ASSET_RESOURCE_TYPES_V1)}"
        ) from exc

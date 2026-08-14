"""VersionAssetRef helpers for append fingerprints and binding validation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from aieos.domains.content.application.errors import AssetReferenceValidationFailed
from aieos.domains.content.application.ports import AssetReferenceValidationPort
from aieos.domains.content.domain.errors import InvalidVersionAssetRefError
from aieos.domains.content.domain.identities import ContentId, ContentVersionId
from aieos.domains.content.domain.version_asset_ref import VersionAssetRef
from aieos.platform.resources import InvalidResourceRefError, ResourceRef


def _as_resource_ref(value: object) -> ResourceRef:
    if isinstance(value, ResourceRef):
        return value
    if not isinstance(value, Mapping):
        raise AssetReferenceValidationFailed("asset reference invalid")
    try:
        return ResourceRef(
            resource_type=value["resource_type"],  # type: ignore[arg-type,index]
            resource_id=value["resource_id"],  # type: ignore[arg-type,index]
            resource_revision=value.get("resource_revision"),  # type: ignore[arg-type]
        )
    except (KeyError, TypeError, InvalidResourceRefError) as exc:
        raise AssetReferenceValidationFailed("asset reference invalid") from exc


def _normalize_fingerprint_item(ref: Mapping[str, Any] | VersionAssetRef) -> dict[str, object]:
    if isinstance(ref, VersionAssetRef):
        return {
            "role": ref.role,
            "ordinal": ref.ordinal,
            "resource_type": ref.resource_ref.resource_type,
            "resource_id": str(ref.resource_ref.resource_id),
            "resource_revision": ref.resource_ref.resource_revision,
            "required": ref.required,
        }
    resource_ref = _as_resource_ref(ref.get("resource_ref", ref))
    try:
        role = ref["role"]
        ordinal = ref["ordinal"]
        required = ref["required"]
    except KeyError as exc:
        raise AssetReferenceValidationFailed("asset reference invalid") from exc
    return {
        "role": role,
        "ordinal": ordinal,
        "resource_type": resource_ref.resource_type,
        "resource_id": str(resource_ref.resource_id),
        "resource_revision": resource_ref.resource_revision,
        "required": required,
    }


def canonical_asset_ref_fingerprint_items(
    refs: Sequence[Mapping[str, Any] | VersionAssetRef],
) -> list[dict[str, object]]:
    items = [_normalize_fingerprint_item(ref) for ref in refs]
    return sorted(
        items,
        key=lambda item: (
            str(item["role"]),
            int(item["ordinal"]),  # type: ignore[arg-type]
            str(item["resource_type"]),
            str(item["resource_id"]),
            -1 if item["resource_revision"] is None else int(item["resource_revision"]),  # type: ignore[arg-type]
            bool(item["required"]),
        ),
    )


def ensure_unique_asset_slots(refs: Sequence[VersionAssetRef]) -> None:
    """Reject duplicate (role, ordinal) before persistence."""
    seen: set[tuple[str, int]] = set()
    for ref in refs:
        slot = (ref.role, ref.ordinal)
        if slot in seen:
            raise AssetReferenceValidationFailed("duplicate asset reference slot")
        seen.add(slot)


def build_version_asset_refs(
    *,
    tenant_id: UUID,
    content_id: ContentId,
    version_id: ContentVersionId,
    created_at: datetime,
    items: Sequence[Mapping[str, Any] | VersionAssetRef],
) -> tuple[VersionAssetRef, ...]:
    built: list[VersionAssetRef] = []
    for item in items:
        if isinstance(item, VersionAssetRef):
            ref = item
            if (
                ref.tenant_id != tenant_id
                or ref.content_id != content_id
                or ref.version_id != version_id
            ):
                raise AssetReferenceValidationFailed("asset reference invalid")
        else:
            resource_ref = _as_resource_ref(item.get("resource_ref", item))
            try:
                ref = VersionAssetRef(
                    tenant_id=tenant_id,
                    content_id=content_id,
                    version_id=version_id,
                    resource_ref=resource_ref,
                    role=item["role"],  # type: ignore[arg-type,index]
                    ordinal=item["ordinal"],  # type: ignore[arg-type,index]
                    required=item["required"],  # type: ignore[arg-type,index]
                    created_at=created_at,
                )
            except (KeyError, TypeError, InvalidVersionAssetRefError) as exc:
                raise AssetReferenceValidationFailed("asset reference invalid") from exc
        built.append(ref)
    ensure_unique_asset_slots(built)
    return tuple(built)


def validate_asset_bindings(
    port: AssetReferenceValidationPort,
    tenant_id: UUID,
    principal_id: UUID,
    resource_refs: Sequence[ResourceRef],
) -> None:
    for resource_ref in resource_refs:
        try:
            port.validate_binding(
                tenant_id=tenant_id,
                principal_id=principal_id,
                resource_ref=resource_ref,
            )
        except AssetReferenceValidationFailed:
            raise

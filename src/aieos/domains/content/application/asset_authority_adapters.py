"""Content Asset governance adapters backed by AssetUseAuthority (ADR-AIEOS-032)."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from uuid import UUID

from aieos.domains.content.application.errors import (
    AssetReferenceValidationFailed,
    PublicationAssetValidationFailed,
)
from aieos.domains.content.domain.identities import ContentId, ContentVersionId
from aieos.domains.content.domain.version_asset_ref import VersionAssetRef
from aieos.platform.governance.errors import GovernanceUnavailableError
from aieos.platform.resources import ResourceRef
from aieos.platform.resources.asset_use import (
    AssetUseAssessment,
    AssetUseAuthority,
)


def _require_exact_handled_types(handled: Collection[str]) -> frozenset[str]:
    catalog = frozenset(handled)
    if not catalog:
        raise ValueError("handled_resource_types must be a non-empty exact set")
    for resource_type in catalog:
        if not isinstance(resource_type, str) or not resource_type:
            raise ValueError("handled resource types must be non-empty strings")
        if "*" in resource_type:
            raise ValueError("wildcard resource types are not permitted")
    return catalog


class AssetAuthorityReferenceValidationAdapter:
    """Production AssetReferenceValidationPort backed by AssetUseAuthority."""

    def __init__(
        self,
        authority: AssetUseAuthority,
        *,
        handled_resource_types: Collection[str],
    ) -> None:
        self._authority = authority
        self._handled = _require_exact_handled_types(handled_resource_types)

    def validate_binding(
        self, *, tenant_id: UUID, principal_id: UUID, resource_ref: ResourceRef
    ) -> None:
        if resource_ref.resource_type not in self._handled:
            raise AssetReferenceValidationFailed("asset reference invalid")
        assessment = self._assess(
            tenant_id=tenant_id,
            principal_id=principal_id,
            resource_ref=resource_ref,
        )
        if not assessment.usable:
            raise AssetReferenceValidationFailed("asset reference invalid")

    def _assess(
        self,
        *,
        tenant_id: UUID,
        principal_id: UUID,
        resource_ref: ResourceRef,
    ) -> AssetUseAssessment:
        try:
            result = self._authority.assess_use(
                tenant_id=tenant_id,
                principal_id=principal_id,
                resource_ref=resource_ref,
            )
        except GovernanceUnavailableError:
            raise
        if not isinstance(result, AssetUseAssessment):
            raise GovernanceUnavailableError("governance unavailable")
        return result


class AssetAuthorityCurrentGovernanceAdapter:
    """Production AssetCurrentGovernancePort backed by AssetUseAuthority."""

    def __init__(
        self,
        authority: AssetUseAuthority,
        *,
        handled_resource_types: Collection[str],
    ) -> None:
        self._authority = authority
        self._handled = _require_exact_handled_types(handled_resource_types)

    def validate_current_use(
        self,
        *,
        tenant_id: UUID,
        principal_id: UUID,
        content_id: ContentId,
        version_id: ContentVersionId,
        asset_refs: Sequence[VersionAssetRef],
    ) -> None:
        _ = (content_id, version_id)
        memo: dict[ResourceRef, AssetUseAssessment] = {}
        for ref in asset_refs:
            resource_ref = ref.resource_ref
            if resource_ref.resource_type not in self._handled:
                raise PublicationAssetValidationFailed(
                    "publication asset validation failed"
                )
            assessment = memo.get(resource_ref)
            if assessment is None:
                assessment = self._assess(
                    tenant_id=tenant_id,
                    principal_id=principal_id,
                    resource_ref=resource_ref,
                )
                memo[resource_ref] = assessment
            if not assessment.usable:
                raise PublicationAssetValidationFailed(
                    "publication asset validation failed"
                )

    def _assess(
        self,
        *,
        tenant_id: UUID,
        principal_id: UUID,
        resource_ref: ResourceRef,
    ) -> AssetUseAssessment:
        try:
            result = self._authority.assess_use(
                tenant_id=tenant_id,
                principal_id=principal_id,
                resource_ref=resource_ref,
            )
        except GovernanceUnavailableError:
            raise
        if not isinstance(result, AssetUseAssessment):
            raise GovernanceUnavailableError("governance unavailable")
        return result

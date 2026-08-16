"""Shared PED-I10A test helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from aieos.platform.governance.errors import GovernanceUnavailableError
from aieos.platform.resources import ResourceRef
from aieos.platform.resources.asset_use import AssetUseAssessment


@dataclass
class RecordingAssetUseAuthority:
    """Test-only AssetUseAuthority. Never wire into production composition."""

    assessments: dict[UUID, AssetUseAssessment] = field(default_factory=dict)
    default: AssetUseAssessment = field(
        default_factory=lambda: AssetUseAssessment(usable=True)
    )
    unavailable: bool = False
    raise_runtime: bool = False
    malformed: object | None = None
    calls: list[tuple[UUID, UUID, ResourceRef]] = field(default_factory=list)

    def assess_use(
        self,
        *,
        tenant_id: UUID,
        principal_id: UUID,
        resource_ref: ResourceRef,
    ) -> AssetUseAssessment:
        self.calls.append((tenant_id, principal_id, resource_ref))
        if self.raise_runtime:
            raise RuntimeError("SECRET_ASSET_AUTHORITY_BUG")
        if self.unavailable:
            raise GovernanceUnavailableError("governance unavailable")
        if self.malformed is not None:
            return self.malformed  # type: ignore[return-value]
        return self.assessments.get(resource_ref.resource_id, self.default)

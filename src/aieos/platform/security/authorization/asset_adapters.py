"""Asset authorization port adapters backed by AuthorizationKernel.

Asset capability vocabulary is owned by
``aieos.domains.asset.application.ports`` — this module composes the
injected catalog from those canonical constants and does not redefine them.
"""

from __future__ import annotations

from uuid import UUID

from aieos.domains.asset.application.mutation_errors import AssetForbidden
from aieos.domains.asset.application.ports import (
    ASSET_CREATE,
    ASSET_LIFECYCLE_MANAGE,
    ASSET_QUARANTINE_MANAGE,
    ASSET_REVISION_ACTIVATE,
    ASSET_REVISION_REGISTER,
    ASSET_SAFETY_DECIDE,
)
from aieos.domains.asset.domain.identities import AssetId
from aieos.platform.security.authorization.decisions import AuthorityDecision
from aieos.platform.security.authorization.kernel import (
    AuthorizationKernel,
    capability_contains_wildcard,
)
from aieos.platform.security.context import AuthorizationUnavailableError

# Code-governed Asset capability catalog (composition only; not a DB catalog).
AIEOS_ASSET_CAPABILITIES: frozenset[str] = frozenset(
    {
        ASSET_CREATE,
        ASSET_REVISION_REGISTER,
        ASSET_REVISION_ACTIVATE,
        ASSET_LIFECYCLE_MANAGE,
        ASSET_QUARANTINE_MANAGE,
        ASSET_SAFETY_DECIDE,
    }
)


class KernelAssetMutationAuthorization:
    """AuthorizationKernel adapter for Asset mutation commands."""

    def __init__(self, kernel: AuthorizationKernel) -> None:
        self._kernel = kernel

    def authorize(
        self,
        *,
        tenant_id: UUID,
        principal_id: UUID,
        capability: str,
        asset_id: AssetId | None = None,
    ) -> None:
        _ = asset_id  # resource context only; not authority
        if capability_contains_wildcard(capability) or capability not in (
            AIEOS_ASSET_CAPABILITIES
        ):
            raise AssetForbidden("asset capability denied")
        try:
            decision = self._kernel.decide_capability(
                principal_id=principal_id,
                tenant_id=tenant_id,
                capability=capability,
            )
        except AuthorizationUnavailableError:
            raise
        except Exception as exc:
            raise AuthorizationUnavailableError(
                "authorization unavailable"
            ) from exc
        if decision is AuthorityDecision.ALLOW:
            return
        raise AssetForbidden("asset capability denied")

"""Production CurrentTenantAccessAuthority backed by AuthorizationKernel."""

from __future__ import annotations

from uuid import UUID

from aieos.platform.security.authorization.decisions import AuthorityDecision
from aieos.platform.security.authorization.kernel import AuthorizationKernel
from aieos.platform.security.context import (
    AuthorizationUnavailableError,
    UnauthorizedError,
)


class KernelCurrentTenantAccessAuthority:
    """ADR-AIEOS-031 current tenant membership authority. No membership cache."""

    def __init__(self, kernel: AuthorizationKernel) -> None:
        self._kernel = kernel

    def authorize_tenant(self, *, principal_id: UUID, tenant_id: UUID) -> None:
        try:
            decision = self._kernel.decide_tenant_access(
                principal_id=principal_id, tenant_id=tenant_id
            )
        except AuthorizationUnavailableError:
            raise
        except Exception as exc:
            raise AuthorizationUnavailableError(
                "authorization unavailable"
            ) from exc
        if decision is AuthorityDecision.ALLOW:
            return
        raise UnauthorizedError("not authorized for requested tenant")

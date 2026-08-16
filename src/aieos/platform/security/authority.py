"""Current tenant-access authority and SecurityContext resolver.

CURRENT means CURRENT: consulted on every TrustedSecurityContext resolution.
No membership caching. Tenant membership ≠ content.review / content.publish.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from aieos.platform.security.context import (
    AuthorizationUnavailableError,
    TrustedSecurityContext,
    UnauthenticatedError,
    UnauthorizedError,
)
from aieos.platform.security.identity import TrustedRequestIdentity


class CurrentTenantAccessAuthority(Protocol):
    """Whether an authenticated principal currently may access a tenant.

    Fail closed. Raise UnauthorizedError when access is denied (including
    suspended/inactive tenants). Raise AuthorizationUnavailableError (or
    TenantAuthorityUnavailableError) when the authority cannot be consulted.
    """

    def authorize_tenant(self, *, principal_id: UUID, tenant_id: UUID) -> None: ...


class CurrentAuthoritySecurityContextResolver:
    """Resolve TrustedSecurityContext from trusted identity + current authority.

    X-AIEOS-Tenant-ID is REQUESTED TENANT ONLY. Client headers are not authority.
    """

    def __init__(self, authority: CurrentTenantAccessAuthority) -> None:
        self._authority = authority

    def resolve(
        self,
        *,
        identity: TrustedRequestIdentity,
        requested_tenant_id: UUID | None,
    ) -> TrustedSecurityContext:
        if requested_tenant_id is None:
            raise UnauthenticatedError("tenant header required")
        try:
            self._authority.authorize_tenant(
                principal_id=identity.principal_id,
                tenant_id=requested_tenant_id,
            )
        except UnauthorizedError:
            raise
        except AuthorizationUnavailableError:
            raise
        except Exception as exc:
            raise AuthorizationUnavailableError(
                "tenant authority unavailable"
            ) from exc
        return TrustedSecurityContext(
            tenant_id=requested_tenant_id,
            principal_id=identity.principal_id,
        )

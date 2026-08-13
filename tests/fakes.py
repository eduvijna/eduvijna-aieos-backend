"""Test doubles. Not production security or catalog implementations."""

from __future__ import annotations

from uuid import UUID

from aieos.platform.security.context import (
    TrustedSecurityContext,
    UnauthenticatedError,
    UnauthorizedError,
)


class StubSecurityContextResolver:
    """Authorized tenant is independent of the caller-supplied tenant header."""

    def __init__(
        self,
        authorized_tenant_id: UUID,
        principal_id: UUID,
        *,
        unauthenticated: bool = False,
    ) -> None:
        self.authorized_tenant_id = authorized_tenant_id
        self.principal_id = principal_id
        self.unauthenticated = unauthenticated

    def resolve(self, requested_tenant_id: UUID | None) -> TrustedSecurityContext:
        if self.unauthenticated:
            raise UnauthenticatedError("not authenticated")
        if requested_tenant_id is None:
            raise UnauthenticatedError("tenant header required")
        if requested_tenant_id != self.authorized_tenant_id:
            raise UnauthorizedError("not authorized for requested tenant")
        return TrustedSecurityContext(
            tenant_id=self.authorized_tenant_id,
            principal_id=self.principal_id,
        )

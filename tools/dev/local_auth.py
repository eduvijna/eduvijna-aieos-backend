"""Local-only authentication adapters for F5 API development.

These adapters are wired exclusively by tools/dev/run_local_api.py.
Production entrypoints must never import this module.

LOCAL DEVELOPMENT ONLY — NEVER PRODUCTION.
"""

from __future__ import annotations

from uuid import UUID

from aieos.platform.security.context import (
    TrustedSecurityContext,
    UnauthenticatedError,
    UnauthorizedError,
)
from aieos.platform.security.identity import TrustedRequestIdentity


class LocalDevelopmentBearerAuthenticator:
    """Accept a fixed local bearer token for Swagger Authorize flows."""

    def __init__(
        self,
        *,
        principal_id: UUID,
        expected_bearer_token: str,
    ) -> None:
        self._principal_id = principal_id
        self._expected_bearer_token = expected_bearer_token

    def authenticate(self, request) -> TrustedRequestIdentity:
        header = request.headers.get("Authorization")
        if header is None:
            raise UnauthenticatedError("authorization header required")
        prefix = "Bearer "
        if not header.startswith(prefix):
            raise UnauthenticatedError("bearer token required")
        token = header[len(prefix) :].strip()
        if token != self._expected_bearer_token:
            raise UnauthorizedError("invalid bearer token")
        return TrustedRequestIdentity(principal_id=self._principal_id)


class LocalDevelopmentTenantSecurityResolver:
    """Resolve tenant context for the fixed local teacher identity."""

    def __init__(self, *, authorized_tenant_id: UUID, principal_id: UUID) -> None:
        self._authorized_tenant_id = authorized_tenant_id
        self._principal_id = principal_id

    def resolve(
        self,
        *,
        identity: TrustedRequestIdentity,
        requested_tenant_id: UUID | None,
    ) -> TrustedSecurityContext:
        if requested_tenant_id is None:
            raise UnauthenticatedError("tenant header required")
        if requested_tenant_id != self._authorized_tenant_id:
            raise UnauthorizedError("not authorized for requested tenant")
        if identity.principal_id != self._principal_id:
            raise UnauthorizedError("principal mismatch")
        return TrustedSecurityContext(
            tenant_id=self._authorized_tenant_id,
            principal_id=identity.principal_id,
        )

"""Trusted security-context projection for HTTP composition."""

from aieos.platform.security.authenticator import RequestIdentityAuthenticator
from aieos.platform.security.authority import (
    CurrentAuthoritySecurityContextResolver,
    CurrentTenantAccessAuthority,
)
from aieos.platform.security.context import (
    AuthenticationUnavailableError,
    AuthorizationUnavailableError,
    SecurityContextResolver,
    TenantAuthorityUnavailableError,
    TrustedSecurityContext,
    UnauthenticatedError,
    UnauthorizedError,
)
from aieos.platform.security.identity import TrustedRequestIdentity

__all__ = [
    "AuthenticationUnavailableError",
    "AuthorizationUnavailableError",
    "CurrentAuthoritySecurityContextResolver",
    "CurrentTenantAccessAuthority",
    "RequestIdentityAuthenticator",
    "SecurityContextResolver",
    "TenantAuthorityUnavailableError",
    "TrustedRequestIdentity",
    "TrustedSecurityContext",
    "UnauthenticatedError",
    "UnauthorizedError",
]

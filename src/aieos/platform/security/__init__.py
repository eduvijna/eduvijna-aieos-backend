"""Trusted security-context projection for HTTP composition."""

from aieos.platform.security.auth_config import (
    AIEOS_PRINCIPAL_ID_CLAIM,
    AuthConfigurationError,
    AuthRuntimeConfig,
    load_auth_runtime_config,
)
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
from aieos.platform.security.jwt_bearer import JwtBearerRequestIdentityAuthenticator

__all__ = [
    "AIEOS_PRINCIPAL_ID_CLAIM",
    "AuthConfigurationError",
    "AuthRuntimeConfig",
    "AuthenticationUnavailableError",
    "AuthorizationUnavailableError",
    "CurrentAuthoritySecurityContextResolver",
    "CurrentTenantAccessAuthority",
    "JwtBearerRequestIdentityAuthenticator",
    "RequestIdentityAuthenticator",
    "SecurityContextResolver",
    "TenantAuthorityUnavailableError",
    "TrustedRequestIdentity",
    "TrustedSecurityContext",
    "UnauthenticatedError",
    "UnauthorizedError",
    "load_auth_runtime_config",
]

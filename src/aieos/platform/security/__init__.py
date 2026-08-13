"""Trusted security-context projection for HTTP composition."""

from aieos.platform.security.context import (
    SecurityContextResolver,
    TrustedSecurityContext,
    UnauthenticatedError,
    UnauthorizedError,
)

__all__ = [
    "SecurityContextResolver",
    "TrustedSecurityContext",
    "UnauthenticatedError",
    "UnauthorizedError",
]

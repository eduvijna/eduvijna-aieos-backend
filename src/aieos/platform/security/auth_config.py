"""ADR-AIEOS-030 authentication configuration contracts.

Fail-closed: no default issuer, audience, or JWKS URI.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlparse

ENV_AUTH_ISSUER = "AIEOS_AUTH_ISSUER"
ENV_AUTH_AUDIENCE = "AIEOS_AUTH_AUDIENCE"
ENV_AUTH_JWKS_URI = "AIEOS_AUTH_JWKS_URI"

AIEOS_PRINCIPAL_ID_CLAIM = "https://eduvijna.com/claims/aieos/principal_id"

_REQUIRED_AUTH_ENV = (
    ENV_AUTH_ISSUER,
    ENV_AUTH_AUDIENCE,
    ENV_AUTH_JWKS_URI,
)

# Absolute HTTPS URI with host; no credentials; no fragment authority tricks.
_HTTPS_HOST = re.compile(
    r"^https://[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?(?::[0-9]+)?(/.*)?$"
)


class AuthConfigurationError(Exception):
    """Fail-closed authentication configuration error."""


@dataclass(frozen=True, slots=True)
class AuthRuntimeConfig:
    """Immutable production authentication verifier configuration."""

    issuer: str
    audience: str
    jwks_uri: str


def _require(environ: Mapping[str, str], name: str) -> str:
    raw = environ.get(name)
    if raw is None or raw.strip() == "":
        raise AuthConfigurationError(f"{name} is required and must be non-empty")
    return raw.strip()


def _parse_jwks_uri(value: str) -> str:
    if not _HTTPS_HOST.fullmatch(value):
        raise AuthConfigurationError(
            f"{ENV_AUTH_JWKS_URI} must be an absolute https URI with a host"
        )
    parsed = urlparse(value)
    if parsed.scheme != "https":
        raise AuthConfigurationError(
            f"{ENV_AUTH_JWKS_URI} must use the https scheme"
        )
    if parsed.username is not None or parsed.password is not None:
        raise AuthConfigurationError(
            f"{ENV_AUTH_JWKS_URI} must not embed credentials"
        )
    if not parsed.hostname:
        raise AuthConfigurationError(
            f"{ENV_AUTH_JWKS_URI} must include a hostname"
        )
    return value


def load_auth_runtime_config(environ: Mapping[str, str]) -> AuthRuntimeConfig:
    """Parse fail-closed ADR-AIEOS-030 authentication configuration."""
    for name in _REQUIRED_AUTH_ENV:
        _require(environ, name)
    issuer = _require(environ, ENV_AUTH_ISSUER)
    audience = _require(environ, ENV_AUTH_AUDIENCE)
    jwks_uri = _parse_jwks_uri(_require(environ, ENV_AUTH_JWKS_URI))
    return AuthRuntimeConfig(issuer=issuer, audience=audience, jwks_uri=jwks_uri)

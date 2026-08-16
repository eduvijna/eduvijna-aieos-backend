"""ADR-AIEOS-030 JWT Bearer production RequestIdentityAuthenticator.

Verifies Authorization: Bearer <JWT access token> against configured issuer,
audience, and JWKS. Establishes principal_id only — never tenant authority,
roles, scopes, or capability snapshots.

PyJWT / cryptography belong only at this platform authentication boundary.
"""

from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID

import jwt
from jwt import PyJWKClient
from jwt.exceptions import (
    DecodeError,
    ExpiredSignatureError,
    ImmatureSignatureError,
    InvalidAlgorithmError,
    InvalidAudienceError,
    InvalidIssuerError,
    InvalidSignatureError,
    InvalidTokenError,
    MissingRequiredClaimError,
    PyJWKClientConnectionError,
    PyJWKClientError,
    PyJWKError,
)
from starlette.requests import Request

from aieos.platform.security.auth_config import (
    AIEOS_PRINCIPAL_ID_CLAIM,
    AuthRuntimeConfig,
)
from aieos.platform.security.context import (
    AuthenticationUnavailableError,
    UnauthenticatedError,
)
from aieos.platform.security.identity import TrustedRequestIdentity

_ALLOWED_ALG = "RS256"
_REQUIRED_CLAIMS = ("exp", "iat", "sub", "client_id", "jti", AIEOS_PRINCIPAL_ID_CLAIM)
_ALLOWED_TYP = frozenset({"JWT", "at+jwt"})


class _SigningKeyProvider(Protocol):
    def get_signing_key_from_jwt(self, token: str) -> Any: ...


def _unauthenticated() -> UnauthenticatedError:
    return UnauthenticatedError("unauthenticated")


def _unavailable() -> AuthenticationUnavailableError:
    return AuthenticationUnavailableError("authentication unavailable")


def _extract_bearer_token(authorization: str | None) -> str:
    if authorization is None:
        raise _unauthenticated()
    # Exact Bearer profile: scheme + single SP + non-empty token credentials.
    if not authorization.startswith("Bearer "):
        raise _unauthenticated()
    token = authorization[len("Bearer ") :]
    if not token or any(ch.isspace() for ch in token):
        raise _unauthenticated()
    # Compact JWS only (access-token JWT profile): three base64url segments.
    parts = token.split(".")
    if len(parts) != 3 or any(part == "" for part in parts):
        raise _unauthenticated()
    return token


def _assert_access_token_header(token: str) -> None:
    try:
        header = jwt.get_unverified_header(token)
    except (DecodeError, InvalidTokenError, ValueError, TypeError):
        raise _unauthenticated() from None
    if not isinstance(header, dict):
        raise _unauthenticated()
    alg = header.get("alg")
    if alg != _ALLOWED_ALG:
        raise _unauthenticated()
    typ = header.get("typ")
    if typ is not None:
        if not isinstance(typ, str) or typ not in _ALLOWED_TYP:
            raise _unauthenticated()


def _require_non_empty_str(claims: dict[str, Any], name: str) -> str:
    value = claims.get(name)
    if not isinstance(value, str) or value.strip() == "":
        raise _unauthenticated()
    return value


def _extract_principal_id(claims: dict[str, Any]) -> UUID:
    raw = claims.get(AIEOS_PRINCIPAL_ID_CLAIM)
    if not isinstance(raw, str) or raw.strip() == "":
        raise _unauthenticated()
    try:
        return UUID(raw)
    except (ValueError, TypeError):
        raise _unauthenticated() from None


class JwtBearerRequestIdentityAuthenticator:
    """Production JWT Bearer authenticator (ADR-AIEOS-030).

    Does not read principal/role/admin/capability headers. Does not authorize
    tenants. Token content cannot choose JWKS URI, issuer, audience, or alg.
    """

    def __init__(
        self,
        config: AuthRuntimeConfig,
        *,
        jwk_client: _SigningKeyProvider | None = None,
    ) -> None:
        self._config = config
        self._jwk_client: _SigningKeyProvider = jwk_client or PyJWKClient(
            config.jwks_uri,
            cache_keys=True,
        )

    def authenticate(self, request: Request) -> TrustedRequestIdentity:
        token = _extract_bearer_token(request.headers.get("Authorization"))
        _assert_access_token_header(token)
        signing_key = self._resolve_signing_key(token)
        claims = self._verify_claims(token, signing_key)
        _require_non_empty_str(claims, "sub")
        _require_non_empty_str(claims, "client_id")
        _require_non_empty_str(claims, "jti")
        principal_id = _extract_principal_id(claims)
        return TrustedRequestIdentity(principal_id=principal_id)

    def _resolve_signing_key(self, token: str) -> Any:
        try:
            return self._jwk_client.get_signing_key_from_jwt(token)
        except PyJWKClientConnectionError as exc:
            raise _unavailable() from exc
        except PyJWKError as exc:
            # Unknown/malformed key material for this token → untrusted.
            raise _unauthenticated() from exc
        except PyJWKClientError as exc:
            # Missing kid after a successful JWKS fetch is unauthenticated;
            # other JWKS client failures are treated as verifier unavailable.
            detail = str(exc).lower()
            if "unable to find a signing key" in detail:
                raise _unauthenticated() from exc
            raise _unavailable() from exc
        except OSError as exc:
            raise _unavailable() from exc
        except Exception as exc:
            raise _unavailable() from exc

    def _verify_claims(self, token: str, signing_key: Any) -> dict[str, Any]:
        key = getattr(signing_key, "key", signing_key)
        try:
            payload = jwt.decode(
                token,
                key,
                algorithms=[_ALLOWED_ALG],
                issuer=self._config.issuer,
                audience=self._config.audience,
                options={
                    "require": list(_REQUIRED_CLAIMS),
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_nbf": True,
                    "verify_iat": True,
                    "verify_aud": True,
                    "verify_iss": True,
                },
            )
        except (
            ExpiredSignatureError,
            ImmatureSignatureError,
            InvalidSignatureError,
            InvalidIssuerError,
            InvalidAudienceError,
            InvalidAlgorithmError,
            MissingRequiredClaimError,
            DecodeError,
            InvalidTokenError,
        ) as exc:
            raise _unauthenticated() from exc
        except Exception as exc:
            # Unexpected verifier/library defect — fail closed without leak.
            raise _unavailable() from exc
        if not isinstance(payload, dict):
            raise _unauthenticated()
        return payload

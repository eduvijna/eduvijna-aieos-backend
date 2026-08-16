"""HTTP/runtime authentication adapter port.

May be Starlette/FastAPI-facing. Do not place Request types in domain/ or
Content business contracts. No production authenticator is provided here.
"""

from __future__ import annotations

from typing import Protocol

from starlette.requests import Request

from aieos.platform.security.identity import TrustedRequestIdentity


class RequestIdentityAuthenticator(Protocol):
    """Explicit production-facing authentication port.

    Production composition must supply an implementation. There is no default,
    anonymous fallback, or AlwaysAuthenticated adapter in src/aieos.
    """

    def authenticate(self, request: Request) -> TrustedRequestIdentity: ...

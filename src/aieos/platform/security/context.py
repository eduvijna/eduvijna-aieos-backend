"""Trusted request security context. Not an Authorization Kernel."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


class SecurityContextError(Exception):
    """Base error from a trusted security-context resolver."""


class UnauthenticatedError(SecurityContextError):
    """The context provider did not authenticate the caller."""


class UnauthorizedError(SecurityContextError):
    """The caller is not authorized for the requested tenant/context."""


@dataclass(frozen=True, slots=True)
class TrustedSecurityContext:
    """Already-authorized tenant and actor. Header values are not authority."""

    tenant_id: UUID
    principal_id: UUID


class SecurityContextResolver(Protocol):
    def resolve(self, requested_tenant_id: UUID | None) -> TrustedSecurityContext: ...

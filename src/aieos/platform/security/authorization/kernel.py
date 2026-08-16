"""Embedded AIEOS Authorization Kernel (ADR-AIEOS-031).

Binary ALLOW/DENY. Default DENY. No cache. No external policy engine.
"""

from __future__ import annotations

from collections.abc import Collection
from uuid import UUID

from sqlalchemy.engine import Engine

from aieos.platform.security.authorization.decisions import (
    AuthorityDecision,
    PrincipalStatus,
    TenantStatus,
)
from aieos.platform.security.authorization.repository import (
    SqlAlchemySecurityAuthorityRepository,
    grant_is_currently_valid,
    membership_is_currently_valid,
)
from aieos.platform.security.context import AuthorizationUnavailableError


class AuthorizationKernel:
    """Current-authority evaluator for tenant membership and exact capabilities."""

    def __init__(
        self,
        engine: Engine,
        *,
        known_capabilities: Collection[str],
        repository: SqlAlchemySecurityAuthorityRepository | None = None,
    ) -> None:
        self._engine = engine
        self._known = frozenset(known_capabilities)
        self._repo = repository or SqlAlchemySecurityAuthorityRepository(engine)

    def decide_tenant_access(
        self, *, principal_id: UUID, tenant_id: UUID
    ) -> AuthorityDecision:
        try:
            bundle = self._repo.load_tenant_access_bundle(
                principal_id=principal_id, tenant_id=tenant_id
            )
        except AuthorizationUnavailableError:
            raise
        except Exception as exc:
            raise AuthorizationUnavailableError(
                "authorization unavailable"
            ) from exc
        if (
            bundle.principal is None
            or bundle.principal.status != PrincipalStatus.ACTIVE
        ):
            return AuthorityDecision.DENY
        if bundle.tenant is None or bundle.tenant.status != TenantStatus.ACTIVE:
            return AuthorityDecision.DENY
        if bundle.membership is None:
            return AuthorityDecision.DENY
        if not membership_is_currently_valid(
            bundle.membership, now=bundle.evaluated_at
        ):
            return AuthorityDecision.DENY
        return AuthorityDecision.ALLOW

    def decide_capability(
        self, *, principal_id: UUID, tenant_id: UUID, capability: str
    ) -> AuthorityDecision:
        if capability not in self._known:
            return AuthorityDecision.DENY
        try:
            bundle = self._repo.load_capability_bundle(
                principal_id=principal_id,
                tenant_id=tenant_id,
                capability=capability,
            )
        except AuthorizationUnavailableError:
            raise
        except Exception as exc:
            raise AuthorizationUnavailableError(
                "authorization unavailable"
            ) from exc
        if (
            bundle.principal is None
            or bundle.principal.status != PrincipalStatus.ACTIVE
        ):
            return AuthorityDecision.DENY
        if bundle.tenant is None or bundle.tenant.status != TenantStatus.ACTIVE:
            return AuthorityDecision.DENY
        if bundle.membership is None:
            return AuthorityDecision.DENY
        if not membership_is_currently_valid(
            bundle.membership, now=bundle.evaluated_at
        ):
            return AuthorityDecision.DENY
        grant = bundle.grant
        if grant is None or not grant_is_currently_valid(
            grant, now=bundle.evaluated_at
        ):
            return AuthorityDecision.DENY
        if grant.capability != capability:
            return AuthorityDecision.DENY
        return AuthorityDecision.ALLOW

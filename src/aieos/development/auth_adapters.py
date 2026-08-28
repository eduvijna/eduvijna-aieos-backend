"""NON_PRODUCTION authorization adapters for development reference tooling.

These adapters MUST NOT be wired into production runtime composition.
They exist solely for explicit developer/test invocation of reference scenarios.

Naming intentionally avoids architecture-forbidden production-fake substrings.
"""

from __future__ import annotations

from uuid import UUID

from aieos.domains.content.application.errors import ReviewForbidden
from aieos.domains.content.application.ports import (
    CONTENT_REVIEW_DECIDE,
    CONTENT_REVIEW_SUBMIT,
)
from aieos.platform.security.context import (
    TrustedSecurityContext,
    UnauthenticatedError,
    UnauthorizedError,
)
from aieos.platform.security.identity import TrustedRequestIdentity


class DevelopmentPrincipalAuthenticator:
    """Development-only fixed principal. Not a production IdP adapter."""

    def __init__(self, principal_id: UUID) -> None:
        self.principal_id = principal_id

    def authenticate(self, request) -> TrustedRequestIdentity:
        return TrustedRequestIdentity(principal_id=self.principal_id)


class DevelopmentTenantSecurityResolver:
    """Development-only tenant resolver matching the test harness contract."""

    def __init__(self, authorized_tenant_id: UUID, principal_id: UUID) -> None:
        self.authorized_tenant_id = authorized_tenant_id
        self.principal_id = principal_id

    def resolve(
        self,
        *,
        identity: TrustedRequestIdentity,
        requested_tenant_id: UUID | None,
    ) -> TrustedSecurityContext:
        if requested_tenant_id is None:
            raise UnauthenticatedError("tenant header required")
        if requested_tenant_id != self.authorized_tenant_id:
            raise UnauthorizedError("not authorized for requested tenant")
        return TrustedSecurityContext(
            tenant_id=self.authorized_tenant_id,
            principal_id=identity.principal_id,
        )


class DevelopmentReviewAuthorizationPermit:
    """Development-only permissive review authorization."""

    def authorize(
        self,
        *,
        tenant_id: UUID,
        principal_id: UUID,
        content_id,
        version_id,
        capability: str,
    ) -> None:
        if capability not in {CONTENT_REVIEW_SUBMIT, CONTENT_REVIEW_DECIDE}:
            raise ReviewForbidden("review capability denied")


class DevelopmentReviewCommentPermit:
    def evaluate(self, comment: str | None) -> None:
        return None


class DevelopmentPublicationAuthorizationPermit:
    def authorize(
        self,
        *,
        tenant_id: UUID,
        principal_id: UUID,
        content_id,
        version_id,
        capability: str,
    ) -> None:
        return None


class DevelopmentPublicationGovernancePermit:
    def evaluate(
        self,
        *,
        tenant_id: UUID,
        content_id,
        version_id,
    ) -> None:
        return None


class DevelopmentAssetReferencePermit:
    def validate_binding(
        self, *, tenant_id: UUID, principal_id: UUID, resource_ref
    ) -> None:
        return None


class DevelopmentAssetCurrentUsePermit:
    def validate_current_use(
        self,
        *,
        tenant_id: UUID,
        principal_id: UUID,
        content_id,
        version_id,
        asset_refs,
    ) -> None:
        return None


class DevelopmentAIGenerationPermit:
    """Development-only AI materialization authorization permit."""

    def authorize(
        self,
        *,
        tenant_id: UUID,
        principal_id: UUID,
        content_id,
        capability: str,
    ) -> None:
        return None

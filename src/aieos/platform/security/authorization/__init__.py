"""ADR-AIEOS-031 embedded production Authorization Kernel package."""

from __future__ import annotations

from aieos.domains.content.application.ports import (
    CONTENT_MIGRATE_IMPORT,
    CONTENT_PUBLISH,
    CONTENT_REVIEW_DECIDE,
    CONTENT_REVIEW_SUBMIT,
    CONTENT_VERSION_CREATE,
)
from aieos.platform.security.authorization.content_adapters import (
    AIEOS_CONTENT_CAPABILITIES,
    KernelAIGenerationAuthorization,
    KernelContentMigrationAuthorization,
    KernelPublicationAuthorization,
    KernelReviewAuthorization,
)
from aieos.platform.security.authorization.decisions import (
    AuthorityDecision,
    GrantStatus,
    MembershipStatus,
    PrincipalStatus,
    TenantStatus,
)
from aieos.platform.security.authorization.kernel import AuthorizationKernel
from aieos.platform.security.authorization.repository import (
    SqlAlchemySecurityAuthorityRepository,
)
from aieos.platform.security.authorization.tenant_authority import (
    KernelCurrentTenantAccessAuthority,
)

__all__ = [
    "AIEOS_CONTENT_CAPABILITIES",
    "CONTENT_MIGRATE_IMPORT",
    "CONTENT_PUBLISH",
    "CONTENT_REVIEW_DECIDE",
    "CONTENT_REVIEW_SUBMIT",
    "CONTENT_VERSION_CREATE",
    "AuthorityDecision",
    "AuthorizationKernel",
    "GrantStatus",
    "KernelAIGenerationAuthorization",
    "KernelContentMigrationAuthorization",
    "KernelCurrentTenantAccessAuthority",
    "KernelPublicationAuthorization",
    "KernelReviewAuthorization",
    "MembershipStatus",
    "PrincipalStatus",
    "SqlAlchemySecurityAuthorityRepository",
    "TenantStatus",
]

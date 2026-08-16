"""ADR-AIEOS-031 authorization decision vocabulary and status constants."""

from __future__ import annotations

from enum import StrEnum


class AuthorityDecision(StrEnum):
    """Binary Authorization Kernel decision. Default is DENY."""

    ALLOW = "ALLOW"
    DENY = "DENY"


class PrincipalStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    DISABLED = "DISABLED"


class TenantStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    DISABLED = "DISABLED"


class MembershipStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    REVOKED = "REVOKED"


class GrantStatus(StrEnum):
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"


# Content capability vocabulary (code-governed; not DB catalog).
CONTENT_REVIEW_SUBMIT = "content.review.submit"
CONTENT_REVIEW_DECIDE = "content.review.decide"
CONTENT_PUBLISH = "content.publish"
CONTENT_VERSION_CREATE = "content.version.create"
CONTENT_MIGRATE_IMPORT = "content.migrate.import"

AIEOS_CONTENT_CAPABILITIES: frozenset[str] = frozenset(
    {
        CONTENT_REVIEW_SUBMIT,
        CONTENT_REVIEW_DECIDE,
        CONTENT_PUBLISH,
        CONTENT_VERSION_CREATE,
        CONTENT_MIGRATE_IMPORT,
    }
)

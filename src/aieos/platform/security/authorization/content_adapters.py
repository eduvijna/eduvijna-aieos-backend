"""Content authorization port adapters backed by AuthorizationKernel.

Content capability vocabulary is owned by
``aieos.domains.content.application.ports`` — this module composes the
injected catalog from those canonical constants and does not redefine them.
"""

from __future__ import annotations

from uuid import UUID

from aieos.domains.content.application.errors import (
    AIGenerationForbidden,
    MigrationForbidden,
    PublicationForbidden,
    ReviewForbidden,
)
from aieos.domains.content.application.ports import (
    CONTENT_MIGRATE_IMPORT,
    CONTENT_PUBLISH,
    CONTENT_REVIEW_DECIDE,
    CONTENT_REVIEW_SUBMIT,
    CONTENT_VERSION_CREATE,
)
from aieos.domains.content.domain.identities import ContentId, ContentVersionId
from aieos.platform.security.authorization.decisions import AuthorityDecision
from aieos.platform.security.authorization.kernel import AuthorizationKernel
from aieos.platform.security.context import AuthorizationUnavailableError

# Code-governed Content capability catalog (composition only; not a DB catalog).
AIEOS_CONTENT_CAPABILITIES: frozenset[str] = frozenset(
    {
        CONTENT_REVIEW_SUBMIT,
        CONTENT_REVIEW_DECIDE,
        CONTENT_PUBLISH,
        CONTENT_VERSION_CREATE,
        CONTENT_MIGRATE_IMPORT,
    }
)

_REVIEW_CAPABILITIES = frozenset({CONTENT_REVIEW_SUBMIT, CONTENT_REVIEW_DECIDE})


class KernelReviewAuthorization:
    def __init__(self, kernel: AuthorizationKernel) -> None:
        self._kernel = kernel

    def authorize(
        self,
        *,
        tenant_id: UUID,
        principal_id: UUID,
        content_id: ContentId,
        version_id: ContentVersionId,
        capability: str,
    ) -> None:
        _ = (content_id, version_id)  # resource context only; not authority
        if capability not in _REVIEW_CAPABILITIES:
            raise ReviewForbidden("review capability denied")
        self._require(
            tenant_id=tenant_id,
            principal_id=principal_id,
            capability=capability,
            forbidden=ReviewForbidden,
            message="review capability denied",
        )

    def _require(
        self,
        *,
        tenant_id: UUID,
        principal_id: UUID,
        capability: str,
        forbidden: type[Exception],
        message: str,
    ) -> None:
        try:
            decision = self._kernel.decide_capability(
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
        if decision is AuthorityDecision.ALLOW:
            return
        raise forbidden(message)


class KernelPublicationAuthorization:
    def __init__(self, kernel: AuthorizationKernel) -> None:
        self._kernel = kernel

    def authorize(
        self,
        *,
        tenant_id: UUID,
        principal_id: UUID,
        content_id: ContentId,
        version_id: ContentVersionId,
        capability: str,
    ) -> None:
        _ = (content_id, version_id)
        if capability != CONTENT_PUBLISH:
            raise PublicationForbidden("publication capability denied")
        try:
            decision = self._kernel.decide_capability(
                principal_id=principal_id,
                tenant_id=tenant_id,
                capability=CONTENT_PUBLISH,
            )
        except AuthorizationUnavailableError:
            raise
        except Exception as exc:
            raise AuthorizationUnavailableError(
                "authorization unavailable"
            ) from exc
        if decision is AuthorityDecision.ALLOW:
            return
        raise PublicationForbidden("publication capability denied")


class KernelAIGenerationAuthorization:
    def __init__(self, kernel: AuthorizationKernel) -> None:
        self._kernel = kernel

    def authorize(
        self,
        *,
        tenant_id: UUID,
        principal_id: UUID,
        content_id: ContentId,
        capability: str,
    ) -> None:
        _ = content_id
        if capability != CONTENT_VERSION_CREATE:
            raise AIGenerationForbidden("AI generation capability denied")
        try:
            decision = self._kernel.decide_capability(
                principal_id=principal_id,
                tenant_id=tenant_id,
                capability=CONTENT_VERSION_CREATE,
            )
        except AuthorizationUnavailableError:
            raise
        except Exception as exc:
            raise AuthorizationUnavailableError(
                "authorization unavailable"
            ) from exc
        if decision is AuthorityDecision.ALLOW:
            return
        raise AIGenerationForbidden("AI generation capability denied")


class KernelContentMigrationAuthorization:
    def __init__(self, kernel: AuthorizationKernel) -> None:
        self._kernel = kernel

    def authorize(
        self,
        *,
        tenant_id: UUID,
        principal_id: UUID,
        capability: str,
    ) -> None:
        if capability != CONTENT_MIGRATE_IMPORT:
            raise MigrationForbidden("migration capability denied")
        try:
            decision = self._kernel.decide_capability(
                principal_id=principal_id,
                tenant_id=tenant_id,
                capability=CONTENT_MIGRATE_IMPORT,
            )
        except AuthorizationUnavailableError:
            raise
        except Exception as exc:
            raise AuthorizationUnavailableError(
                "authorization unavailable"
            ) from exc
        if decision is AuthorityDecision.ALLOW:
            return
        raise MigrationForbidden("migration capability denied")

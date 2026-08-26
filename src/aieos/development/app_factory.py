"""Build a NON_PRODUCTION FastAPI app for Teacher OS review scenario seeding."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from sqlalchemy.engine import Engine

from aieos.development.auth_adapters import (
    DevelopmentAssetCurrentUsePermit,
    DevelopmentAssetReferencePermit,
    DevelopmentPrincipalAuthenticator,
    DevelopmentPublicationAuthorizationPermit,
    DevelopmentPublicationGovernancePermit,
    DevelopmentReviewAuthorizationPermit,
    DevelopmentReviewCommentPermit,
    DevelopmentTenantSecurityResolver,
)
from aieos.development.schemas import (
    DEV_CONTENT_TYPE,
    build_development_schema_registry,
)
from aieos.domains.content.application.catalog import StaticContentTypeCatalog
from aieos.domains.content.infrastructure.persistence.uow import (
    SqlAlchemyContentUnitOfWorkFactory,
)
from aieos.platform.api.app import create_app

CURSOR_KEY = b"tos-dev01-development-cursor-signing-key"
IDEMPOTENCY_RETENTION = timedelta(hours=24)


def build_development_review_scenario_app(
    runtime_engine: Engine,
    *,
    tenant_id: UUID,
    principal_id: UUID,
):
    """Compose create_app with development-only adapters and test.generic catalog.

    Must not be called from production runtime entrypoints.
    """
    return create_app(
        uow_factory=SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
        request_identity_authenticator=DevelopmentPrincipalAuthenticator(principal_id),
        security_resolver=DevelopmentTenantSecurityResolver(tenant_id, principal_id),
        content_types=StaticContentTypeCatalog({DEV_CONTENT_TYPE}),
        cursor_signing_key=CURSOR_KEY,
        schema_registry=build_development_schema_registry(),
        idempotency_retention=IDEMPOTENCY_RETENTION,
        review_authorization=DevelopmentReviewAuthorizationPermit(),
        review_comment_policy=DevelopmentReviewCommentPermit(),
        publication_authorization=DevelopmentPublicationAuthorizationPermit(),
        publication_governance=DevelopmentPublicationGovernancePermit(),
        asset_reference_validation=DevelopmentAssetReferencePermit(),
        asset_current_governance=DevelopmentAssetCurrentUsePermit(),
    )

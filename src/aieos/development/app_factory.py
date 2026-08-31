"""Build a NON_PRODUCTION FastAPI app for Teacher OS development scenarios."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from sqlalchemy.engine import Engine

from aieos.development.auth_adapters import (
    DevelopmentAIGenerationPermit,
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
    build_development_schema_registry,
    development_content_type_names,
)
from aieos.development.school_context import DevelopmentSchoolContextClassReader
from aieos.domains.content.application.catalog import StaticContentTypeCatalog
from aieos.domains.content.infrastructure.persistence.uow import (
    SqlAlchemyContentUnitOfWorkFactory,
)
from aieos.domains.teaching.infrastructure.persistence.uow import (
    SqlAlchemyTeachingUnitOfWorkFactory,
)
from aieos.platform.ai.config import (
    DEFAULT_AI_MODEL,
    DEFAULT_AI_PROVIDER,
    load_generation_lease_seconds,
    load_openai_provider_config_from_env,
)
from aieos.platform.ai.fake import FakeStructuredModelGateway
from aieos.platform.ai.gateway import StructuredModelGateway
from aieos.platform.ai.infrastructure.persistence.uow import (
    SqlAlchemyAIUnitOfWorkFactory,
)
from aieos.platform.ai.providers.openai import OpenAIStructuredModelGateway
from aieos.platform.api.app import create_app

CURSOR_KEY = b"tos-dev01-development-cursor-signing-key"
IDEMPOTENCY_RETENTION = timedelta(hours=24)


def build_development_teacher_os_app(
    runtime_engine: Engine,
    *,
    tenant_id: UUID,
    principal_id: UUID,
    model_gateway: StructuredModelGateway | None = None,
    ai_provider_id: str = DEFAULT_AI_PROVIDER,
    ai_model_id: str = DEFAULT_AI_MODEL,
):
    """Compose create_app with development adapters, schemas, and optional gateway.

    Must not be called from production runtime entrypoints.
    """
    gateway = model_gateway
    provider_id = ai_provider_id
    model_id = ai_model_id
    if gateway is None:
        try:
            config = load_openai_provider_config_from_env()
            gateway = OpenAIStructuredModelGateway(config)
            provider_id = config.provider_id
            model_id = config.model_id
        except ValueError:
            gateway = FakeStructuredModelGateway()
            provider_id = "fake"
            model_id = "fake-model"

    return create_app(
        uow_factory=SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
        teaching_uow_factory=SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine),
        request_identity_authenticator=DevelopmentPrincipalAuthenticator(principal_id),
        security_resolver=DevelopmentTenantSecurityResolver(tenant_id, principal_id),
        content_types=StaticContentTypeCatalog(development_content_type_names()),
        cursor_signing_key=CURSOR_KEY,
        schema_registry=build_development_schema_registry(),
        idempotency_retention=IDEMPOTENCY_RETENTION,
        review_authorization=DevelopmentReviewAuthorizationPermit(),
        review_comment_policy=DevelopmentReviewCommentPermit(),
        publication_authorization=DevelopmentPublicationAuthorizationPermit(),
        publication_governance=DevelopmentPublicationGovernancePermit(),
        asset_reference_validation=DevelopmentAssetReferencePermit(),
        asset_current_governance=DevelopmentAssetCurrentUsePermit(),
        ai_uow_factory=SqlAlchemyAIUnitOfWorkFactory(runtime_engine),
        model_gateway=gateway,
        ai_generation_authorization=DevelopmentAIGenerationPermit(),
        ai_provider_id=provider_id,
        ai_model_id=model_id,
        generation_lease_seconds=load_generation_lease_seconds(),
        school_context_class_reader=DevelopmentSchoolContextClassReader(
            tenant_id=tenant_id
        ),
    )


def build_development_review_scenario_app(
    runtime_engine: Engine,
    *,
    tenant_id: UUID,
    principal_id: UUID,
):
    """Back-compat DEV01/DEV02 helper. Delegates to Teacher OS development app."""
    return build_development_teacher_os_app(
        runtime_engine,
        tenant_id=tenant_id,
        principal_id=principal_id,
        model_gateway=FakeStructuredModelGateway(),
        ai_provider_id="fake",
        ai_model_id="fake-model",
    )

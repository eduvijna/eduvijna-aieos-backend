"""FastAPI application factory. No module-level production singleton."""

from __future__ import annotations

from datetime import timedelta

from fastapi import FastAPI

from aieos.domains.content.api.v1.routes import router as content_v1_router
from aieos.domains.content.application.ai_for_review import (
    CreateAIGeneratedContentForReviewService,
)
from aieos.domains.content.application.ai_preparation_for_review import (
    CreateAIPreparationArtifactsForReviewService,
)
from aieos.domains.content.application.asset_governance import (
    ValidateVersionAssetGovernanceService,
)
from aieos.domains.content.application.create import CreateContentService
from aieos.domains.content.application.http_append import (
    GetContentVersionService,
    HttpAppendContentVersionService,
)
from aieos.domains.content.application.ports import (
    AIGenerationAuthorizationPort,
    AssetCurrentGovernancePort,
    AssetReferenceValidationPort,
    ContentTypeCatalog,
    ContentUnitOfWorkFactory,
    PublicationAuthorizationPort,
    PublicationGovernancePort,
    ReviewAuthorizationPort,
    ReviewCommentPolicy,
)
from aieos.domains.content.application.publish import PublishContentService
from aieos.domains.content.application.queries import GetContentService, ListContentsService
from aieos.domains.content.application.review import ReviewCommandService
from aieos.domains.content.application.review_queue import (
    GetTeacherReviewQueueItemService,
    ListTeacherReviewQueueService,
)
from aieos.domains.content.domain.schema import ContentSchemaRegistry
from aieos.domains.education.application.generate_preparation_kit import (
    GeneratePreparationKitCapability,
)
from aieos.domains.education.application.generate_worksheet import GenerateWorksheetCapability
from aieos.domains.teaching.api.v1.routes import router as teaching_v1_router
from aieos.domains.teaching.application.artifacts import ListTeachingWorkArtifactsService
from aieos.domains.teaching.application.create import CreateTeachingWorkService
from aieos.domains.teaching.application.generate import GenerateTeachingWorkService
from aieos.domains.teaching.application.mission import GetTeacherOsTodayMissionService
from aieos.domains.teaching.application.ports import TeachingUnitOfWorkFactory
from aieos.domains.teaching.application.prepare import PrepareTeachingWorkService
from aieos.domains.teaching.application.queries import (
    GetTeachingWorkService,
    ListTeachingWorksService,
)
from aieos.domains.teaching.application.refine import RefineTeachingWorkService
from aieos.domains.teaching.application.review_queue_port import (
    ReviewQueuePendingCountAdapter,
)
from aieos.platform.ai.application.ports import AIUnitOfWorkFactory
from aieos.platform.ai.clock import UtcNow
from aieos.platform.ai.config import DEFAULT_AI_MODEL, DEFAULT_AI_PROVIDER
from aieos.platform.ai.gateway import StructuredModelGateway
from aieos.platform.api.context import RequestContextMiddleware
from aieos.platform.api.openapi import build_openapi
from aieos.platform.api.pagination import CursorCodec
from aieos.platform.api.problems import install_exception_handlers
from aieos.platform.security.authenticator import RequestIdentityAuthenticator
from aieos.platform.security.context import SecurityContextResolver

_APP_DESCRIPTION = (
    "AIEOS HTTP foundation (GCI-I12, TOS-DEV02, TOS-DEV03, TOS-DEV04). "
    "Content create, version append, review, publish, Teacher OS Review Queue "
    "reads, Teaching Work preparation, Today's Mission projection, AI worksheet "
    "generation, and preparation-kit prepare are development/test foundations "
    "only and MUST NOT be authorized for production until required "
    "security-audit intent persistence is integrated alongside the "
    "transactional outbox."
)


def create_app(
    *,
    uow_factory: ContentUnitOfWorkFactory,
    teaching_uow_factory: TeachingUnitOfWorkFactory,
    request_identity_authenticator: RequestIdentityAuthenticator,
    security_resolver: SecurityContextResolver,
    content_types: ContentTypeCatalog,
    cursor_signing_key: bytes,
    schema_registry: ContentSchemaRegistry,
    idempotency_retention: timedelta,
    review_authorization: ReviewAuthorizationPort,
    review_comment_policy: ReviewCommentPolicy,
    publication_authorization: PublicationAuthorizationPort,
    publication_governance: PublicationGovernancePort,
    asset_reference_validation: AssetReferenceValidationPort,
    asset_current_governance: AssetCurrentGovernancePort,
    ai_uow_factory: AIUnitOfWorkFactory | None = None,
    model_gateway: StructuredModelGateway | None = None,
    ai_generation_authorization: AIGenerationAuthorizationPort | None = None,
    ai_provider_id: str = DEFAULT_AI_PROVIDER,
    ai_model_id: str = DEFAULT_AI_MODEL,
    generation_lease_seconds: int = 120,
    generation_clock: UtcNow | None = None,
) -> FastAPI:
    codec = CursorCodec(cursor_signing_key)
    app = FastAPI(
        title="AIEOS HTTP API",
        version="0.1.0",
        description=_APP_DESCRIPTION,
        docs_url="/docs",
        redoc_url=None,
    )
    install_exception_handlers(app)
    app.add_middleware(RequestContextMiddleware)
    app.include_router(content_v1_router)
    app.include_router(teaching_v1_router)
    app.state.request_identity_authenticator = request_identity_authenticator
    app.state.security_resolver = security_resolver
    app.state.cursor_codec = codec
    app.state.create_content_service = CreateContentService(
        uow_factory, content_types, idempotency_retention=idempotency_retention
    )
    app.state.get_content_service = GetContentService(uow_factory)
    app.state.list_contents_service = ListContentsService(uow_factory)
    app.state.http_append_service = HttpAppendContentVersionService(
        uow_factory,
        schema_registry,
        asset_reference_validation,
        idempotency_retention=idempotency_retention,
    )
    app.state.get_content_version_service = GetContentVersionService(uow_factory)
    app.state.review_command_service = ReviewCommandService(
        uow_factory,
        review_authorization,
        review_comment_policy,
        idempotency_retention=idempotency_retention,
    )
    app.state.publish_content_service = PublishContentService(
        uow_factory,
        publication_authorization,
        publication_governance,
        asset_current_governance,
        schema_registry,
        idempotency_retention=idempotency_retention,
    )
    app.state.validate_version_asset_governance_service = (
        ValidateVersionAssetGovernanceService(uow_factory, asset_current_governance)
    )
    list_teacher_review_queue_service = ListTeacherReviewQueueService(uow_factory)
    app.state.list_teacher_review_queue_service = list_teacher_review_queue_service
    app.state.get_teacher_review_queue_item_service = GetTeacherReviewQueueItemService(
        uow_factory
    )
    app.state.create_teaching_work_service = CreateTeachingWorkService(
        teaching_uow_factory, idempotency_retention=idempotency_retention
    )
    app.state.refine_teaching_work_service = RefineTeachingWorkService(
        teaching_uow_factory, idempotency_retention=idempotency_retention
    )
    app.state.get_teaching_work_service = GetTeachingWorkService(teaching_uow_factory)
    app.state.list_teaching_works_service = ListTeachingWorksService(
        teaching_uow_factory
    )
    app.state.teacher_os_today_mission_service = GetTeacherOsTodayMissionService(
        teaching_uow_factory,
        ReviewQueuePendingCountAdapter(list_teacher_review_queue_service),
    )

    if (
        ai_uow_factory is not None
        and model_gateway is not None
        and ai_generation_authorization is not None
    ):
        create_ai_for_review = CreateAIGeneratedContentForReviewService(
            uow_factory,
            content_types,
            schema_registry,
            asset_reference_validation,
            ai_generation_authorization,
        )
        create_preparation_for_review = CreateAIPreparationArtifactsForReviewService(
            uow_factory,
            content_types,
            schema_registry,
            asset_reference_validation,
            ai_generation_authorization,
        )
        app.state.create_ai_generated_content_for_review_service = create_ai_for_review
        app.state.generate_teaching_work_service = GenerateTeachingWorkService(
            teaching_uow_factory,
            ai_uow_factory,
            uow_factory,
            GenerateWorksheetCapability(model_gateway),
            create_ai_for_review,
            provider_id=ai_provider_id,
            model_id=ai_model_id,
            lease_seconds=generation_lease_seconds,
            clock=generation_clock,
        )
        app.state.prepare_teaching_work_service = PrepareTeachingWorkService(
            teaching_uow_factory,
            ai_uow_factory,
            uow_factory,
            GeneratePreparationKitCapability(model_gateway),
            create_preparation_for_review,
            provider_id=ai_provider_id,
            model_id=ai_model_id,
            lease_seconds=generation_lease_seconds,
            clock=generation_clock,
        )
        app.state.list_teaching_work_artifacts_service = ListTeachingWorkArtifactsService(
            teaching_uow_factory,
            ai_uow_factory,
            uow_factory,
        )
    else:
        app.state.create_ai_generated_content_for_review_service = None
        app.state.generate_teaching_work_service = None
        app.state.prepare_teaching_work_service = None
        app.state.list_teaching_work_artifacts_service = None

    def _openapi() -> dict:
        if app.openapi_schema is None:
            app.openapi_schema = build_openapi(app)
        return app.openapi_schema

    app.openapi = _openapi  # type: ignore[method-assign]
    return app

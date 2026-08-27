"""FastAPI application factory. No module-level production singleton."""

from __future__ import annotations

from datetime import timedelta

from fastapi import FastAPI

from aieos.domains.content.api.v1.routes import router as content_v1_router
from aieos.domains.content.application.asset_governance import (
    ValidateVersionAssetGovernanceService,
)
from aieos.domains.content.application.create import CreateContentService
from aieos.domains.content.application.http_append import (
    GetContentVersionService,
    HttpAppendContentVersionService,
)
from aieos.domains.content.application.ports import (
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
from aieos.domains.teaching.api.v1.routes import router as teaching_v1_router
from aieos.domains.teaching.application.create import CreateTeachingWorkService
from aieos.domains.teaching.application.mission import GetTeacherOsTodayMissionService
from aieos.domains.teaching.application.ports import TeachingUnitOfWorkFactory
from aieos.domains.teaching.application.queries import (
    GetTeachingWorkService,
    ListTeachingWorksService,
)
from aieos.domains.teaching.application.refine import RefineTeachingWorkService
from aieos.domains.teaching.application.review_queue_port import (
    ReviewQueuePendingCountAdapter,
)
from aieos.platform.api.context import RequestContextMiddleware
from aieos.platform.api.openapi import build_openapi
from aieos.platform.api.pagination import CursorCodec
from aieos.platform.api.problems import install_exception_handlers
from aieos.platform.security.authenticator import RequestIdentityAuthenticator
from aieos.platform.security.context import SecurityContextResolver

_APP_DESCRIPTION = (
    "AIEOS HTTP foundation (GCI-I12, TOS-DEV02). "
    "Content create, version append, review, publish, Teacher OS Review Queue "
    "reads, Teaching Work preparation, and Today's Mission projection reads are "
    "development/test foundations only and MUST NOT be authorized for "
    "production until required security-audit intent persistence is integrated "
    "alongside the transactional outbox."
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
    # Today's Mission is derived per request: the Review Queue projection plus
    # durable Teaching Work rows. Nothing below persists a mission.
    app.state.teacher_os_today_mission_service = GetTeacherOsTodayMissionService(
        teaching_uow_factory,
        ReviewQueuePendingCountAdapter(list_teacher_review_queue_service),
    )

    def _openapi() -> dict:
        if app.openapi_schema is None:
            app.openapi_schema = build_openapi(app)
        return app.openapi_schema

    app.openapi = _openapi  # type: ignore[method-assign]
    return app

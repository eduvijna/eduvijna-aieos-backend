"""FastAPI application factory. No module-level production singleton."""

from __future__ import annotations

from datetime import timedelta

from fastapi import FastAPI

from aieos.domains.content.api.v1.routes import router as content_v1_router
from aieos.domains.content.application.create import CreateContentService
from aieos.domains.content.application.http_append import (
    GetContentVersionService,
    HttpAppendContentVersionService,
)
from aieos.domains.content.application.ports import (
    ContentTypeCatalog,
    ContentUnitOfWorkFactory,
    ReviewAuthorizationPort,
    ReviewCommentPolicy,
)
from aieos.domains.content.application.queries import GetContentService, ListContentsService
from aieos.domains.content.application.review import ReviewCommandService
from aieos.domains.content.domain.schema import ContentSchemaRegistry
from aieos.platform.api.context import RequestContextMiddleware
from aieos.platform.api.openapi import build_openapi
from aieos.platform.api.pagination import CursorCodec
from aieos.platform.api.problems import install_exception_handlers
from aieos.platform.security.context import SecurityContextResolver

_APP_DESCRIPTION = (
    "AIEOS HTTP foundation (GCI-I08). "
    "Content create, version append, and review mutations are development/test "
    "foundations only and MUST NOT be authorized for production until required "
    "security-audit intent persistence is integrated alongside the transactional outbox."
)


def create_app(
    *,
    uow_factory: ContentUnitOfWorkFactory,
    security_resolver: SecurityContextResolver,
    content_types: ContentTypeCatalog,
    cursor_signing_key: bytes,
    schema_registry: ContentSchemaRegistry,
    idempotency_retention: timedelta,
    review_authorization: ReviewAuthorizationPort,
    review_comment_policy: ReviewCommentPolicy,
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
    app.state.security_resolver = security_resolver
    app.state.cursor_codec = codec
    app.state.create_content_service = CreateContentService(
        uow_factory, content_types, idempotency_retention=idempotency_retention
    )
    app.state.get_content_service = GetContentService(uow_factory)
    app.state.list_contents_service = ListContentsService(uow_factory)
    app.state.http_append_service = HttpAppendContentVersionService(
        uow_factory, schema_registry, idempotency_retention=idempotency_retention
    )
    app.state.get_content_version_service = GetContentVersionService(uow_factory)
    app.state.review_command_service = ReviewCommandService(
        uow_factory,
        review_authorization,
        review_comment_policy,
        idempotency_retention=idempotency_retention,
    )

    def _openapi() -> dict:
        if app.openapi_schema is None:
            app.openapi_schema = build_openapi(app)
        return app.openapi_schema

    app.openapi = _openapi  # type: ignore[method-assign]
    return app

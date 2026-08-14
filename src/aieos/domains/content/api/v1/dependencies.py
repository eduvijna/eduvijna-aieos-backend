"""HTTP dependencies. Tenant header is resolver input, not tenant authority."""

from __future__ import annotations

from typing import Annotated

from fastapi import Header, Request

from aieos.domains.content.application.create import CreateContentService
from aieos.domains.content.application.http_append import (
    GetContentVersionService,
    HttpAppendContentVersionService,
)
from aieos.domains.content.application.queries import GetContentService, ListContentsService
from aieos.platform.api.context import TENANT_ID_HEADER, parse_requested_tenant_id
from aieos.platform.api.pagination import CursorCodec
from aieos.platform.security.context import SecurityContextResolver, TrustedSecurityContext


def resolve_trusted_context(
    request: Request,
    x_aieos_tenant_id: Annotated[str | None, Header(alias=TENANT_ID_HEADER)] = None,
) -> TrustedSecurityContext:
    resolver: SecurityContextResolver = request.app.state.security_resolver
    requested = parse_requested_tenant_id(x_aieos_tenant_id)
    return resolver.resolve(requested_tenant_id=requested)


def create_content_service(request: Request) -> CreateContentService:
    return request.app.state.create_content_service


def get_content_service(request: Request) -> GetContentService:
    return request.app.state.get_content_service


def list_contents_service(request: Request) -> ListContentsService:
    return request.app.state.list_contents_service


def cursor_codec(request: Request) -> CursorCodec:
    return request.app.state.cursor_codec


def http_append_service(request: Request) -> HttpAppendContentVersionService:
    return request.app.state.http_append_service


def get_content_version_service(request: Request) -> GetContentVersionService:
    return request.app.state.get_content_version_service


def review_command_service(request: Request):
    return request.app.state.review_command_service


def publish_content_service(request: Request):
    return request.app.state.publish_content_service

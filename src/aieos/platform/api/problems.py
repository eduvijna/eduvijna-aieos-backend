"""RFC 9457 Problem Details responses. Never leak infrastructure exceptions."""

from __future__ import annotations

import re
import uuid
from typing import Any
from uuid import UUID

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from aieos.domains.content.application.errors import (
    AggregateRevisionConflict,
    ContentAlreadyExists,
    ContentApplicationError,
    ContentNotFound,
    InvalidContentRequest,
    PersistenceInvariantViolation,
    PersistenceOperationFailed,
    TenantContextMismatch,
    UnknownContentType,
    VersionAlreadyExists,
    VersionLineageConflict,
)
from aieos.platform.api.context import (
    InvalidTenantHeaderError,
    bind_response_context,
)
from aieos.platform.api.pagination import InvalidCursorError
from aieos.platform.security.context import UnauthenticatedError, UnauthorizedError

PROBLEM_JSON = "application/problem+json"
PROBLEM_TYPE_PREFIX = "urn:aieos:problem:"

_LEAK_PATTERNS = (
    re.compile(r"sqlalchemy", re.I),
    re.compile(r"psycopg", re.I),
    re.compile(r"postgresql(\+psycopg)?://", re.I),
    re.compile(r"\bSELECT\b", re.I),
    re.compile(r"\bINSERT\b", re.I),
    re.compile(r"\bUPDATE\b", re.I),
    re.compile(r"\bDELETE\b", re.I),
    re.compile(r"traceback", re.I),
    re.compile(r"password", re.I),
)


class ProblemErrorItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pointer: str
    code: str
    detail: str


class ProblemDetails(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    title: str
    status: int
    detail: str
    instance: str
    code: str
    request_id: UUID
    correlation_id: UUID
    errors: list[ProblemErrorItem] | None = Field(default=None)


def _context_ids(request: Request) -> tuple[UUID, UUID]:
    request_id = getattr(request.state, "request_id", None) or uuid.uuid7()
    correlation_id = getattr(request.state, "correlation_id", None) or uuid.uuid7()
    return request_id, correlation_id


def _sanitize(text: str) -> str:
    if any(pattern.search(text) for pattern in _LEAK_PATTERNS):
        return "Request could not be completed"
    return text


def problem_payload(
    *,
    status: int,
    code: str,
    title: str,
    detail: str,
    instance: str,
    request_id: UUID,
    correlation_id: UUID,
    errors: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "type": f"{PROBLEM_TYPE_PREFIX}{code}",
        "title": title,
        "status": status,
        "detail": _sanitize(detail),
        "instance": instance,
        "code": code,
        "request_id": str(request_id),
        "correlation_id": str(correlation_id),
    }
    if errors:
        body["errors"] = [
            {
                "pointer": item["pointer"],
                "code": item["code"],
                "detail": _sanitize(item["detail"]),
            }
            for item in errors
        ]
    return body


def problem_response(
    request: Request,
    *,
    status: int,
    code: str,
    title: str,
    detail: str,
    request_id: UUID | None = None,
    correlation_id: UUID | None = None,
    errors: list[dict[str, str]] | None = None,
) -> JSONResponse:
    rid, cid = _context_ids(request)
    if request_id is not None:
        rid = request_id
    if correlation_id is not None:
        cid = correlation_id
    payload = problem_payload(
        status=status,
        code=code,
        title=title,
        detail=detail,
        instance=request.url.path,
        request_id=rid,
        correlation_id=cid,
        errors=errors,
    )
    response = JSONResponse(payload, status_code=status, media_type=PROBLEM_JSON)
    bind_response_context(response.headers, request_id=rid, correlation_id=cid)
    return response


_APPLICATION_PROBLEMS: dict[type[ContentApplicationError], tuple[int, str, str, str]] = {
    ContentNotFound: (
        404,
        "content_not_found",
        "Content not found",
        "Content was not found",
    ),
    UnknownContentType: (
        422,
        "unknown_content_type",
        "Unknown content type",
        "content_type is not registered",
    ),
    InvalidContentRequest: (
        422,
        "invalid_content_request",
        "Invalid content request",
        "Content request is invalid",
    ),
    ContentAlreadyExists: (
        409,
        "content_already_exists",
        "Content already exists",
        "Content already exists",
    ),
    VersionAlreadyExists: (
        409,
        "version_already_exists",
        "ContentVersion already exists",
        "ContentVersion already exists",
    ),
    PersistenceOperationFailed: (
        503,
        "persistence_unavailable",
        "Persistence unavailable",
        "Content persistence is temporarily unavailable",
    ),
    PersistenceInvariantViolation: (
        422,
        "persistence_invariant_violation",
        "Persistence invariant violated",
        "A content persistence invariant was violated",
    ),
    AggregateRevisionConflict: (
        409,
        "aggregate_revision_conflict",
        "Aggregate revision conflict",
        "Expected aggregate revision does not match stored head",
    ),
    VersionLineageConflict: (
        422,
        "version_lineage_conflict",
        "Version lineage conflict",
        "Append would violate linear ContentVersion history",
    ),
    TenantContextMismatch: (
        403,
        "tenant_context_mismatch",
        "Tenant context mismatch",
        "Execution tenant does not match the resource tenant",
    ),
}


def install_exception_handlers(app) -> None:
    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = []
        for err in exc.errors():
            loc = [str(part) for part in err.get("loc", ()) if part != "body"]
            pointer = "/" + "/".join(loc) if loc else "/"
            errors.append(
                {
                    "pointer": pointer,
                    "code": "field_invalid",
                    "detail": str(err.get("msg", "invalid")),
                }
            )
        return problem_response(
            request,
            status=422,
            code="validation_error",
            title="Request validation failed",
            detail="Request validation failed",
            errors=errors,
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        if exc.status_code == 404:
            return problem_response(
                request,
                status=404,
                code="not_found",
                title="Not found",
                detail="The requested resource was not found",
            )
        if exc.status_code == 405:
            return problem_response(
                request,
                status=405,
                code="method_not_allowed",
                title="Method not allowed",
                detail="The method is not allowed for this resource",
            )
        return problem_response(
            request,
            status=exc.status_code,
            code="http_error",
            title="Request failed",
            detail="Request could not be completed",
        )

    @app.exception_handler(ContentApplicationError)
    async def application_handler(
        request: Request, exc: ContentApplicationError
    ) -> JSONResponse:
        mapping = _APPLICATION_PROBLEMS.get(type(exc))
        if mapping is None:
            status, code, title, detail = (
                500,
                "internal_error",
                "Internal error",
                "An unexpected error occurred",
            )
        else:
            status, code, title, detail = mapping
        return problem_response(
            request,
            status=status,
            code=code,
            title=title,
            detail=detail,
        )

    @app.exception_handler(UnauthenticatedError)
    async def unauthenticated_handler(
        request: Request, exc: UnauthenticatedError
    ) -> JSONResponse:
        return problem_response(
            request,
            status=401,
            code="unauthenticated",
            title="Unauthenticated",
            detail="Authentication is required",
        )

    @app.exception_handler(UnauthorizedError)
    async def unauthorized_handler(request: Request, exc: UnauthorizedError) -> JSONResponse:
        return problem_response(
            request,
            status=403,
            code="forbidden",
            title="Forbidden",
            detail="Not authorized for the requested tenant",
        )

    @app.exception_handler(InvalidTenantHeaderError)
    async def tenant_header_handler(
        request: Request, exc: InvalidTenantHeaderError
    ) -> JSONResponse:
        return problem_response(
            request,
            status=400,
            code="invalid_tenant_header",
            title="Invalid tenant header",
            detail="X-AIEOS-Tenant-ID must be a UUID",
        )

    @app.exception_handler(InvalidCursorError)
    async def cursor_handler(request: Request, exc: InvalidCursorError) -> JSONResponse:
        return problem_response(
            request,
            status=400,
            code="invalid_cursor",
            title="Invalid cursor",
            detail="The list cursor is invalid",
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        if isinstance(
            exc,
            (
                StarletteHTTPException,
                RequestValidationError,
                ContentApplicationError,
                UnauthenticatedError,
                UnauthorizedError,
                InvalidCursorError,
                InvalidTenantHeaderError,
            ),
        ):
            raise exc
        return problem_response(
            request,
            status=500,
            code="internal_error",
            title="Internal error",
            detail="An unexpected error occurred",
        )

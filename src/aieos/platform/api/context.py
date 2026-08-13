"""Server-controlled request_id and correlation context."""

from __future__ import annotations

import uuid
from collections.abc import MutableMapping
from uuid import UUID

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "X-AIEOS-Request-ID"
CORRELATION_ID_HEADER = "X-AIEOS-Correlation-ID"
TENANT_ID_HEADER = "X-AIEOS-Tenant-ID"


class InvalidCorrelationIdError(Exception):
    """Caller-supplied correlation identifier is not a UUID."""


class InvalidTenantHeaderError(Exception):
    """Caller-supplied tenant header is not a UUID."""


def parse_correlation_id(raw: str | None) -> UUID:
    if raw is None or not raw.strip():
        return uuid.uuid7()
    try:
        return UUID(raw.strip())
    except ValueError as exc:
        raise InvalidCorrelationIdError("X-AIEOS-Correlation-ID is not a valid UUID") from exc


def parse_requested_tenant_id(raw: str | None) -> UUID | None:
    if raw is None or not raw.strip():
        return None
    try:
        return UUID(raw.strip())
    except ValueError as exc:
        raise InvalidTenantHeaderError("X-AIEOS-Tenant-ID is not a valid UUID") from exc


def bind_response_context(
    headers: MutableMapping[str, str],
    *,
    request_id: UUID,
    correlation_id: UUID,
) -> None:
    headers[REQUEST_ID_HEADER] = str(request_id)
    headers[CORRELATION_ID_HEADER] = str(correlation_id)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assign a server UUIDv7 request_id. Never trust inbound X-AIEOS-Request-ID."""

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = uuid.uuid7()
        request.state.request_id = request_id
        raw_corr = request.headers.get(CORRELATION_ID_HEADER)
        try:
            correlation_id = parse_correlation_id(raw_corr)
        except InvalidCorrelationIdError:
            from aieos.platform.api.problems import problem_response

            generated = uuid.uuid7()
            request.state.correlation_id = generated
            return problem_response(
                request,
                status=400,
                code="invalid_correlation_id",
                title="Invalid correlation identifier",
                detail="X-AIEOS-Correlation-ID must be a UUID",
                request_id=request_id,
                correlation_id=generated,
            )
        request.state.correlation_id = correlation_id
        response = await call_next(request)
        bind_response_context(
            response.headers,
            request_id=request_id,
            correlation_id=correlation_id,
        )
        return response

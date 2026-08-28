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
    ContentPayloadInvalid,
    ContentSchemaMismatch,
    ContentSchemaNotFound,
    ContentVersionAlreadyPublished,
    ContentVersionAppendNotAllowed,
    ContentVersionNotFound,
    IdempotencyKeyReused,
    InvalidContentRequest,
    PersistenceInvariantViolation,
    PersistenceOperationFailed,
    PublicationApprovalRequired,
    PublicationAssetValidationFailed,
    PublicationForbidden,
    PublicationGovernanceRejected,
    PublicationNotAllowed,
    PublicationPayloadInvalid,
    PublicationSchemaUnavailable,
    PublicationVersionNotCurrent,
    AssetReferenceValidationFailed,
    ReviewAlreadyDecided,
    ReviewCommentRejected,
    ReviewDecisionNotAllowed,
    ReviewForbidden,
    ReviewQueueItemNotFound,
    ReviewQueueInvalidRequest,
    ReviewRequiresNewVersion,
    ReviewSubmitNotAllowed,
    ReviewVersionNotCurrent,
    TenantContextMismatch,
    UnknownContentType,
    VersionAlreadyExists,
    VersionLineageConflict,
    WorkflowCoordinationFailed,
)
from aieos.domains.teaching.application.errors import (
    AggregateRevisionConflict as TeachingAggregateRevisionConflict,
)
from aieos.domains.teaching.application.errors import (
    ContentMaterializationFailedError,
    EducationalQualityFailedError,
    GenerationIdempotencyConflict,
    GenerationServiceUnavailable,
    IdempotencyKeyReused as TeachingIdempotencyKeyReused,
)
from aieos.domains.teaching.application.errors import (
    InvalidTeachingWorkRequest,
    ModelGenerationFailedError,
    ModelOutputInvalidError,
    ModelProviderUnavailableError,
    PersistenceInvariantViolation as TeachingPersistenceInvariantViolation,
    PersistenceOperationFailed as TeachingPersistenceOperationFailed,
    TeacherOsMissionUnavailable,
    TeachingApplicationError,
    TeachingWorkForbidden,
    TeachingWorkNotFound,
    WorkGenerationAlreadyExists,
    WorkGenerationInProgress,
    WorkGenerationPreconditionRequired,
    WorkGenerationRevisionConflict,
)
from aieos.platform.api.context import (
    InvalidTenantHeaderError,
    bind_response_context,
)
from aieos.platform.api.http_errors import (
    IdempotencyKeyRequiredError,
    InvalidIdempotencyKeyError,
    InvalidIfMatchError,
    PreconditionRequiredError,
)
from aieos.platform.api.pagination import InvalidCursorError
from aieos.platform.governance.errors import GovernanceUnavailableError
from aieos.platform.security.context import (
    AuthenticationUnavailableError,
    AuthorizationUnavailableError,
    UnauthenticatedError,
    UnauthorizedError,
)

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
    ContentVersionNotFound: (
        404,
        "content_version_not_found",
        "ContentVersion not found",
        "ContentVersion was not found",
    ),
    ReviewQueueItemNotFound: (
        404,
        "review_queue_item_not_found",
        "Review Queue item not found",
        "Review Queue item was not found",
    ),
    ReviewQueueInvalidRequest: (
        400,
        "invalid_content_request",
        "Invalid content request",
        "Review Queue request is invalid",
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
        412,
        "resource_revision_conflict",
        "Resource revision conflict",
        "If-Match does not match the current aggregate revision",
    ),
    ContentVersionAppendNotAllowed: (
        409,
        "content_version_append_not_allowed",
        "ContentVersion append not allowed",
        "ContentVersion append is not allowed in the current stewardship state",
    ),
    IdempotencyKeyReused: (
        409,
        "idempotency_key_reused",
        "Idempotency key reused",
        "Idempotency-Key was already used with a different request",
    ),
    ContentSchemaNotFound: (
        422,
        "content_schema_not_found",
        "Content schema not found",
        "schema_id/schema_version is not registered",
    ),
    ContentSchemaMismatch: (
        422,
        "content_schema_mismatch",
        "Content schema mismatch",
        "Registered schema does not match Content.content_type",
    ),
    ContentPayloadInvalid: (
        422,
        "content_payload_invalid",
        "Content payload invalid",
        "payload failed schema validation",
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
    ReviewForbidden: (
        403,
        "forbidden",
        "Forbidden",
        "The principal is not authorized for this review capability",
    ),
    ReviewCommentRejected: (
        422,
        "review_comment_rejected",
        "Review comment rejected",
        "The review comment was rejected by governance policy",
    ),
    ReviewSubmitNotAllowed: (
        409,
        "review_submit_not_allowed",
        "Review submit not allowed",
        "Submit-for-review is not allowed in the current stewardship state",
    ),
    ReviewDecisionNotAllowed: (
        409,
        "review_decision_not_allowed",
        "Review decision not allowed",
        "A review decision is not allowed in the current stewardship state",
    ),
    ReviewVersionNotCurrent: (
        409,
        "review_version_not_current",
        "Review version not current",
        "The requested version is not the current ContentVersion",
    ),
    ReviewRequiresNewVersion: (
        409,
        "review_requires_new_version",
        "Review requires new version",
        "This ContentVersion already has a terminal ReviewDecision",
    ),
    ReviewAlreadyDecided: (
        409,
        "review_already_decided",
        "Review already decided",
        "This ContentVersion already has a terminal ReviewDecision",
    ),
    WorkflowCoordinationFailed: (
        500,
        "workflow_coordination_failed",
        "Workflow coordination failed",
        "Internal workflow coordination failed",
    ),
    PublicationForbidden: (
        403,
        "forbidden",
        "Forbidden",
        "The principal is not authorized for content.publish",
    ),
    PublicationVersionNotCurrent: (
        409,
        "publication_version_not_current",
        "Publication version not current",
        "The requested version is not the current ContentVersion",
    ),
    PublicationApprovalRequired: (
        409,
        "publication_approval_required",
        "Publication approval required",
        "Exact-version APPROVE ReviewDecision is required before publish",
    ),
    ContentVersionAlreadyPublished: (
        409,
        "content_version_already_published",
        "ContentVersion already published",
        "This ContentVersion already has a Publication",
    ),
    PublicationAssetValidationFailed: (
        409,
        "publication_asset_validation_failed",
        "Publication asset validation failed",
        "Required publication asset validation failed",
    ),
    AssetReferenceValidationFailed: (
        422,
        "asset_reference_invalid",
        "Asset reference invalid",
        "One or more asset references failed validation",
    ),
    PublicationGovernanceRejected: (
        409,
        "publication_governance_rejected",
        "Publication governance rejected",
        "Publication governance rejected the candidate",
    ),
    PublicationPayloadInvalid: (
        409,
        "publication_payload_invalid",
        "Publication payload invalid",
        "Stored ContentVersion payload failed schema validation",
    ),
    PublicationSchemaUnavailable: (
        503,
        "publication_schema_unavailable",
        "Publication schema unavailable",
        "Stored schema reader is unavailable for publication",
    ),
    PublicationNotAllowed: (
        409,
        "publication_not_allowed",
        "Publication not allowed",
        "Publish is not allowed in the current stewardship state",
    ),
}

_TEACHING_PROBLEMS: dict[type[TeachingApplicationError], tuple[int, str, str, str]] = {
    TeachingWorkNotFound: (
        404,
        "teaching_work_not_found",
        "Teaching Work not found",
        "Teaching Work was not found",
    ),
    TeachingWorkForbidden: (
        403,
        "forbidden",
        "Forbidden",
        "Teaching Work is owned by a different teacher",
    ),
    TeachingAggregateRevisionConflict: (
        412,
        "resource_revision_conflict",
        "Resource revision conflict",
        "If-Match does not match the current aggregate revision",
    ),
    WorkGenerationRevisionConflict: (
        412,
        "work_generation_revision_conflict",
        "Work generation revision conflict",
        "If-Match does not match the current Work revision",
    ),
    WorkGenerationPreconditionRequired: (
        428,
        "work_generation_precondition_required",
        "Work generation precondition required",
        "If-Match is required for generation",
    ),
    WorkGenerationInProgress: (
        409,
        "work_generation_in_progress",
        "Work generation in progress",
        "A generation with this idempotency key is already in progress",
    ),
    WorkGenerationAlreadyExists: (
        409,
        "work_generation_already_exists",
        "Work generation already exists",
        "This Work already has a successful generation artifact",
    ),
    GenerationIdempotencyConflict: (
        409,
        "generation_idempotency_conflict",
        "Generation idempotency conflict",
        "Idempotency-Key was already used with a different request",
    ),
    ModelProviderUnavailableError: (
        503,
        "model_provider_unavailable",
        "Model provider unavailable",
        "The model provider is temporarily unavailable",
    ),
    ModelGenerationFailedError: (
        502,
        "model_generation_failed",
        "Model generation failed",
        "Model generation failed",
    ),
    ContentMaterializationFailedError: (
        502,
        "content_materialization_failed",
        "Content materialization failed",
        "Generated content could not be materialized for review",
    ),
    ModelOutputInvalidError: (
        502,
        "model_output_invalid",
        "Model output invalid",
        "Model output could not be parsed into the required schema",
    ),
    EducationalQualityFailedError: (
        422,
        "educational_quality_failed",
        "Educational quality failed",
        "Educational Quality Baseline rejected the generated draft",
    ),
    GenerationServiceUnavailable: (
        503,
        "generation_service_unavailable",
        "Generation service unavailable",
        "Teaching Work generation is not composed in this runtime",
    ),
    InvalidTeachingWorkRequest: (
        422,
        "invalid_teaching_work_request",
        "Invalid Teaching Work request",
        "Teaching Work request is invalid",
    ),
    TeachingIdempotencyKeyReused: (
        409,
        "idempotency_key_reused",
        "Idempotency key reused",
        "Idempotency-Key was already used with a different request",
    ),
    TeachingPersistenceOperationFailed: (
        503,
        "persistence_unavailable",
        "Persistence unavailable",
        "Teaching persistence is temporarily unavailable",
    ),
    TeachingPersistenceInvariantViolation: (
        422,
        "persistence_invariant_violation",
        "Persistence invariant violated",
        "A Teaching persistence invariant was violated",
    ),
    TeacherOsMissionUnavailable: (
        503,
        "teacher_os_mission_unavailable",
        "Today's Mission unavailable",
        "Today's Mission projection is temporarily unavailable",
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

    @app.exception_handler(TeachingApplicationError)
    async def teaching_application_handler(
        request: Request, exc: TeachingApplicationError
    ) -> JSONResponse:
        mapping = _TEACHING_PROBLEMS.get(type(exc))
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

    @app.exception_handler(AuthenticationUnavailableError)
    async def authentication_unavailable_handler(
        request: Request, exc: AuthenticationUnavailableError
    ) -> JSONResponse:
        return problem_response(
            request,
            status=503,
            code="authentication_unavailable",
            title="Authentication unavailable",
            detail="Authentication is temporarily unavailable",
        )

    @app.exception_handler(AuthorizationUnavailableError)
    async def authorization_unavailable_handler(
        request: Request, exc: AuthorizationUnavailableError
    ) -> JSONResponse:
        return problem_response(
            request,
            status=503,
            code="authorization_unavailable",
            title="Authorization unavailable",
            detail="Authorization is temporarily unavailable",
        )

    @app.exception_handler(GovernanceUnavailableError)
    async def governance_unavailable_handler(
        request: Request, exc: GovernanceUnavailableError
    ) -> JSONResponse:
        return problem_response(
            request,
            status=503,
            code="governance_unavailable",
            title="Governance unavailable",
            detail="Governance is temporarily unavailable",
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

    @app.exception_handler(PreconditionRequiredError)
    async def precondition_handler(
        request: Request, exc: PreconditionRequiredError
    ) -> JSONResponse:
        return problem_response(
            request,
            status=428,
            code="precondition_required",
            title="Precondition required",
            detail="If-Match is required",
        )

    @app.exception_handler(InvalidIfMatchError)
    async def invalid_if_match_handler(
        request: Request, exc: InvalidIfMatchError
    ) -> JSONResponse:
        return problem_response(
            request,
            status=400,
            code="invalid_if_match",
            title="Invalid If-Match",
            detail="If-Match must be one strong revision validator",
        )

    @app.exception_handler(IdempotencyKeyRequiredError)
    async def idempotency_required_handler(
        request: Request, exc: IdempotencyKeyRequiredError
    ) -> JSONResponse:
        return problem_response(
            request,
            status=400,
            code="idempotency_key_required",
            title="Idempotency-Key required",
            detail="Idempotency-Key is required",
        )

    @app.exception_handler(InvalidIdempotencyKeyError)
    async def invalid_idempotency_handler(
        request: Request, exc: InvalidIdempotencyKeyError
    ) -> JSONResponse:
        return problem_response(
            request,
            status=400,
            code="invalid_idempotency_key",
            title="Invalid Idempotency-Key",
            detail="Idempotency-Key is invalid",
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        if isinstance(
            exc,
            (
                StarletteHTTPException,
                RequestValidationError,
                ContentApplicationError,
                TeachingApplicationError,
                UnauthenticatedError,
                UnauthorizedError,
                AuthenticationUnavailableError,
                AuthorizationUnavailableError,
                GovernanceUnavailableError,
                InvalidCursorError,
                InvalidTenantHeaderError,
                PreconditionRequiredError,
                InvalidIfMatchError,
                IdempotencyKeyRequiredError,
                InvalidIdempotencyKeyError,
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

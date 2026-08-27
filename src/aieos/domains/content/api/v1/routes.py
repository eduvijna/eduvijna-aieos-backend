"""Content HTTP v1. Calls application services only."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response

from aieos.domains.content.api.v1.dependencies import (
    create_content_service,
    cursor_codec,
    get_content_service,
    get_content_version_service,
    get_teacher_review_queue_item_service,
    http_append_service,
    list_contents_service,
    list_teacher_review_queue_service,
    publish_content_service,
    resolve_trusted_context,
    review_command_service,
)
from aieos.domains.content.api.v1.models import (
    ContentCreateRequest,
    ContentListResponse,
    ContentPublishRequest,
    ContentResponse,
    ContentVersionAppendRequest,
    ContentVersionResponse,
    PublicationResponse,
    ReviewDecisionRequest,
    ReviewDecisionResponse,
    ReviewSubmissionResponse,
    TeacherReviewQueueDetailResponse,
    TeacherReviewQueueItemResponse,
    TeacherReviewQueueListResponse,
)
from aieos.domains.content.application.create import CreateContentService
from aieos.domains.content.application.audit import api_mutation_audit_provenance
from aieos.domains.content.application.errors import InvalidContentRequest
from aieos.domains.content.application.http_append import (
    GetContentVersionService,
    HttpAppendContentVersionService,
)
from aieos.domains.content.application.models import (
    ContentReadModel,
    ContentVersionReadModel,
    CreateContentCommand,
    ListContentsQuery,
    PublicationResult,
    ReviewDecisionResult,
    ReviewSubmissionResult,
)
from aieos.domains.content.application.publish import PublishContentService
from aieos.domains.content.application.queries import GetContentService, ListContentsService
from aieos.domains.content.application.review import ReviewCommandService
from aieos.domains.content.application.review_queue import (
    GetTeacherReviewQueueItemService,
    ListTeacherReviewQueueService,
)
from aieos.domains.content.application.review_queue_models import (
    ListTeacherReviewQueueQuery,
    TeacherReviewQueueDetail,
    TeacherReviewQueueItem,
)
from aieos.domains.content.domain.errors import InvalidContentIdentityError
from aieos.domains.content.domain.identities import (
    AggregateRevision,
    ContentId,
    ContentVersionId,
)
from aieos.platform.api.etag import encode_revision_etag
from aieos.platform.api.idempotency_key import parse_idempotency_key
from aieos.platform.api.if_match import parse_if_match
from aieos.platform.api.pagination import CursorCodec, ListCursor, ReviewQueueCursor
from aieos.platform.api.problems import ProblemDetails
from aieos.platform.events.models import MutationEventContext
from aieos.platform.security.context import TrustedSecurityContext


def _mutation_event_context(
    request: Request, context: TrustedSecurityContext
) -> MutationEventContext:
    return MutationEventContext(
        correlation_id=request.state.correlation_id,
        causation_id=request.state.request_id,
        actor_principal_id=context.principal_id,
        effective_actor_id=context.principal_id,
    )

DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 100

router = APIRouter(prefix="/api/v1", tags=["contents"])

def _problem_responses(*statuses: int) -> dict[int, dict[str, object]]:
    return {
        status: {"model": ProblemDetails, "description": "RFC 9457 Problem Details"}
        for status in statuses
    }


_CREATE_RESPONSES = _problem_responses(400, 401, 403, 409, 422, 500, 503)
_GET_RESPONSES = _problem_responses(400, 401, 403, 404, 422, 500, 503)
_LIST_RESPONSES = _problem_responses(400, 401, 403, 422, 500, 503)
_APPEND_RESPONSES = _problem_responses(
    400, 401, 403, 404, 409, 412, 422, 428, 500, 503
)
_VERSION_GET_RESPONSES = _problem_responses(400, 401, 403, 404, 422, 500, 503)
_REVIEW_RESPONSES = _problem_responses(
    400, 401, 403, 404, 409, 412, 422, 428, 500, 503
)
_PUBLISH_RESPONSES = _problem_responses(
    400, 401, 403, 404, 409, 412, 422, 428, 500, 503
)


def _to_response(model: ContentReadModel) -> ContentResponse:
    return ContentResponse(
        content_id=model.content_id.value,
        content_type=model.content_type,
        title=model.title,
        description=model.description,
        locale=model.locale,
        stewardship_state=model.stewardship_state,
        current_version_id=(
            None if model.current_version_id is None else model.current_version_id.value
        ),
        published_version_id=(
            None
            if model.published_version_id is None
            else model.published_version_id.value
        ),
        aggregate_revision=int(model.aggregate_revision),
        created_at=model.created_at,
        updated_at=model.updated_at,
        archived_at=model.archived_at,
    )


def _to_version_response(model: ContentVersionReadModel) -> ContentVersionResponse:
    return ContentVersionResponse(
        version_id=model.version_id.value,
        content_id=model.content_id.value,
        version_number=int(model.version_number),
        parent_version_id=(
            None if model.parent_version_id is None else model.parent_version_id.value
        ),
        schema_id=model.schema_id,
        schema_version=model.schema_version,
        payload=dict(model.payload),
        payload_sha256=model.payload_sha256,
        origin=model.origin,
        created_at=model.created_at,
    )


def _content_id(value: UUID) -> ContentId:
    try:
        return ContentId(value)
    except InvalidContentIdentityError as exc:
        raise InvalidContentRequest("content_id must be a UUIDv7") from exc


def _version_id(value: UUID) -> ContentVersionId:
    try:
        return ContentVersionId(value)
    except InvalidContentIdentityError as exc:
        raise InvalidContentRequest("version_id must be a UUIDv7") from exc


@router.post(
    "/contents",
    status_code=201,
    response_model=ContentResponse,
    operation_id="content_create",
    responses=_CREATE_RESPONSES,
)
def content_create(
    body: ContentCreateRequest,
    request: Request,
    response: Response,
    context: Annotated[TrustedSecurityContext, Depends(resolve_trusted_context)],
    service: Annotated[CreateContentService, Depends(create_content_service)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ContentResponse:
    key = parse_idempotency_key(idempotency_key)
    model = service.create(
        context.tenant_id,
        context.principal_id,
        CreateContentCommand(
            content_type=body.content_type,
            title=body.title,
            description=body.description,
            locale=body.locale,
        ),
        idempotency_key=key,
        event_context=_mutation_event_context(request, context),
        audit_provenance=api_mutation_audit_provenance(context.principal_id),
    )
    response.headers["Location"] = f"/api/v1/contents/{model.content_id}"
    response.headers["ETag"] = encode_revision_etag(int(model.aggregate_revision))
    return _to_response(model)


@router.get(
    "/contents/{content_id}",
    response_model=ContentResponse,
    operation_id="content_get",
    responses=_GET_RESPONSES,
)
def content_get(
    content_id: UUID,
    response: Response,
    context: Annotated[TrustedSecurityContext, Depends(resolve_trusted_context)],
    service: Annotated[GetContentService, Depends(get_content_service)],
) -> ContentResponse:
    model = service.get(context.tenant_id, _content_id(content_id))
    response.headers["ETag"] = encode_revision_etag(int(model.aggregate_revision))
    return _to_response(model)


@router.get(
    "/contents",
    response_model=ContentListResponse,
    operation_id="content_list",
    responses=_LIST_RESPONSES,
)
def content_list(
    context: Annotated[TrustedSecurityContext, Depends(resolve_trusted_context)],
    service: Annotated[ListContentsService, Depends(list_contents_service)],
    codec: Annotated[CursorCodec, Depends(cursor_codec)],
    limit: Annotated[int | None, Query(ge=1)] = None,
    cursor: str | None = None,
) -> ContentListResponse:
    if limit is not None and limit > MAX_LIST_LIMIT:
        raise InvalidContentRequest("list limit exceeds the maximum of 100")
    page_size = DEFAULT_LIST_LIMIT if limit is None else limit
    after_created_at = None
    after_content_id = None
    if cursor is not None:
        decoded = codec.decode(cursor, expected_tenant_id=context.tenant_id)
        after_created_at = decoded.created_at
        after_content_id = ContentId(decoded.content_id)
    result = service.list(
        context.tenant_id,
        ListContentsQuery(
            limit=page_size,
            after_created_at=after_created_at,
            after_content_id=after_content_id,
        ),
    )
    items = [_to_response(item) for item in result.items]
    next_cursor = None
    if result.has_more and result.items:
        last = result.items[-1]
        next_cursor = codec.encode(
            ListCursor(
                tenant_id=context.tenant_id,
                created_at=last.created_at,
                content_id=last.content_id.value,
            )
        )
    return ContentListResponse(items=items, next_cursor=next_cursor)


@router.post(
    "/contents/{content_id}/versions",
    status_code=201,
    response_model=ContentVersionResponse,
    operation_id="content_version_append",
    responses=_APPEND_RESPONSES,
)
def content_version_append(
    content_id: UUID,
    body: ContentVersionAppendRequest,
    request: Request,
    response: Response,
    context: Annotated[TrustedSecurityContext, Depends(resolve_trusted_context)],
    service: Annotated[HttpAppendContentVersionService, Depends(http_append_service)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ContentVersionResponse:
    key = parse_idempotency_key(idempotency_key)
    expected = AggregateRevision(parse_if_match(if_match))
    model, revision = service.append(
        context.tenant_id,
        context.principal_id,
        content_id=_content_id(content_id),
        expected_aggregate_revision=expected,
        schema_id=body.schema_id,
        schema_version=body.schema_version,
        payload=body.payload,
        idempotency_key=key,
        event_context=_mutation_event_context(request, context),
        audit_provenance=api_mutation_audit_provenance(context.principal_id),
        asset_refs=[ref.model_dump(mode="python") for ref in body.asset_refs],
    )
    response.headers["Location"] = (
        f"/api/v1/contents/{model.content_id}/versions/{model.version_id}"
    )
    response.headers["ETag"] = encode_revision_etag(int(revision))
    return _to_version_response(model)


@router.get(
    "/contents/{content_id}/versions/{version_id}",
    response_model=ContentVersionResponse,
    operation_id="content_version_get",
    responses=_VERSION_GET_RESPONSES,
)
def content_version_get(
    content_id: UUID,
    version_id: UUID,
    context: Annotated[TrustedSecurityContext, Depends(resolve_trusted_context)],
    service: Annotated[GetContentVersionService, Depends(get_content_version_service)],
) -> ContentVersionResponse:
    model = service.get(
        context.tenant_id,
        _content_id(content_id),
        _version_id(version_id),
    )
    return _to_version_response(model)


def _to_submission_response(model: ReviewSubmissionResult) -> ReviewSubmissionResponse:
    return ReviewSubmissionResponse(
        content_id=model.content_id.value,
        version_id=model.version_id.value,
        stewardship_state=model.stewardship_state,
        aggregate_revision=int(model.aggregate_revision),
    )


def _to_decision_response(model: ReviewDecisionResult) -> ReviewDecisionResponse:
    return ReviewDecisionResponse(
        review_decision_id=model.review_decision_id.value,
        content_id=model.content_id.value,
        version_id=model.version_id.value,
        decision=model.decision,
        reason_code=model.reason_code,
        comment=model.comment,
        decided_at=model.decided_at,
        stewardship_state=model.stewardship_state,
        aggregate_revision=int(model.aggregate_revision),
    )


def _decide_http(
    action,
    content_id: UUID,
    version_id: UUID,
    request: Request,
    response: Response,
    body: ReviewDecisionRequest,
    context: TrustedSecurityContext,
    if_match: str | None,
    idempotency_key: str | None,
) -> ReviewDecisionResponse:
    key = parse_idempotency_key(idempotency_key)
    expected = AggregateRevision(parse_if_match(if_match))
    model = action(
        context.tenant_id,
        context.principal_id,
        content_id=_content_id(content_id),
        version_id=_version_id(version_id),
        expected_aggregate_revision=expected,
        reason_code=body.reason_code,
        comment=body.comment,
        idempotency_key=key,
        event_context=_mutation_event_context(request, context),
        audit_provenance=api_mutation_audit_provenance(context.principal_id),
    )
    response.headers["ETag"] = encode_revision_etag(int(model.aggregate_revision))
    return _to_decision_response(model)


@router.post(
    "/contents/{content_id}/versions/{version_id}/actions/submit-for-review",
    status_code=200,
    response_model=ReviewSubmissionResponse,
    operation_id="content_review_submit",
    responses=_REVIEW_RESPONSES,
)
def content_review_submit(
    content_id: UUID,
    version_id: UUID,
    request: Request,
    response: Response,
    context: Annotated[TrustedSecurityContext, Depends(resolve_trusted_context)],
    service: Annotated[ReviewCommandService, Depends(review_command_service)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ReviewSubmissionResponse:
    key = parse_idempotency_key(idempotency_key)
    expected = AggregateRevision(parse_if_match(if_match))
    model = service.submit(
        context.tenant_id,
        context.principal_id,
        content_id=_content_id(content_id),
        version_id=_version_id(version_id),
        expected_aggregate_revision=expected,
        idempotency_key=key,
        event_context=_mutation_event_context(request, context),
        audit_provenance=api_mutation_audit_provenance(context.principal_id),
    )
    response.headers["ETag"] = encode_revision_etag(int(model.aggregate_revision))
    return _to_submission_response(model)


@router.post(
    "/contents/{content_id}/versions/{version_id}/actions/approve",
    status_code=200,
    response_model=ReviewDecisionResponse,
    operation_id="content_review_approve",
    responses=_REVIEW_RESPONSES,
)
def content_review_approve(
    content_id: UUID,
    version_id: UUID,
    request: Request,
    response: Response,
    body: ReviewDecisionRequest,
    context: Annotated[TrustedSecurityContext, Depends(resolve_trusted_context)],
    service: Annotated[ReviewCommandService, Depends(review_command_service)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ReviewDecisionResponse:
    return _decide_http(
        service.approve,
        content_id,
        version_id,
        request,
        response,
        body,
        context,
        if_match,
        idempotency_key,
    )


@router.post(
    "/contents/{content_id}/versions/{version_id}/actions/request-changes",
    status_code=200,
    response_model=ReviewDecisionResponse,
    operation_id="content_review_request_changes",
    responses=_REVIEW_RESPONSES,
)
def content_review_request_changes(
    content_id: UUID,
    version_id: UUID,
    request: Request,
    response: Response,
    body: ReviewDecisionRequest,
    context: Annotated[TrustedSecurityContext, Depends(resolve_trusted_context)],
    service: Annotated[ReviewCommandService, Depends(review_command_service)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ReviewDecisionResponse:
    return _decide_http(
        service.request_changes,
        content_id,
        version_id,
        request,
        response,
        body,
        context,
        if_match,
        idempotency_key,
    )


@router.post(
    "/contents/{content_id}/versions/{version_id}/actions/reject",
    status_code=200,
    response_model=ReviewDecisionResponse,
    operation_id="content_review_reject",
    responses=_REVIEW_RESPONSES,
)
def content_review_reject(
    content_id: UUID,
    version_id: UUID,
    request: Request,
    response: Response,
    body: ReviewDecisionRequest,
    context: Annotated[TrustedSecurityContext, Depends(resolve_trusted_context)],
    service: Annotated[ReviewCommandService, Depends(review_command_service)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ReviewDecisionResponse:
    return _decide_http(
        service.reject,
        content_id,
        version_id,
        request,
        response,
        body,
        context,
        if_match,
        idempotency_key,
    )


def _to_publication_response(model: PublicationResult) -> PublicationResponse:
    return PublicationResponse(
        publication_id=model.publication_id.value,
        content_id=model.content_id.value,
        version_id=model.version_id.value,
        approval_decision_id=model.approval_decision_id.value,
        published_at=model.published_at,
        published_version_id=model.published_version_id.value,
        aggregate_revision=int(model.aggregate_revision),
    )


@router.post(
    "/contents/{content_id}/actions/publish",
    status_code=200,
    response_model=PublicationResponse,
    operation_id="content_publish",
    responses=_PUBLISH_RESPONSES,
)
def content_publish(
    content_id: UUID,
    request: Request,
    response: Response,
    body: ContentPublishRequest,
    context: Annotated[TrustedSecurityContext, Depends(resolve_trusted_context)],
    service: Annotated[PublishContentService, Depends(publish_content_service)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> PublicationResponse:
    key = parse_idempotency_key(idempotency_key)
    expected = AggregateRevision(parse_if_match(if_match))
    model = service.publish(
        context.tenant_id,
        context.principal_id,
        content_id=_content_id(content_id),
        version_id=_version_id(body.version_id),
        expected_aggregate_revision=expected,
        idempotency_key=key,
        event_context=_mutation_event_context(request, context),
        audit_provenance=api_mutation_audit_provenance(context.principal_id),
    )
    response.headers["ETag"] = encode_revision_etag(int(model.aggregate_revision))
    return _to_publication_response(model)


def _to_queue_item(model: TeacherReviewQueueItem) -> TeacherReviewQueueItemResponse:
    return TeacherReviewQueueItemResponse(
        content_id=model.content_id.value,
        version_id=model.version_id.value,
        version_number=int(model.version_number),
        content_type=model.content_type,
        title=model.title,
        description=model.description,
        locale=model.locale,
        artifact_status=model.artifact_status,
        origin=model.origin,
        aggregate_revision=int(model.aggregate_revision),
        submitted_at=model.submitted_at,
        version_created_at=model.version_created_at,
        published_version_id=(
            None
            if model.published_version_id is None
            else model.published_version_id.value
        ),
    )


def _to_queue_detail(
    model: TeacherReviewQueueDetail,
) -> TeacherReviewQueueDetailResponse:
    return TeacherReviewQueueDetailResponse(
        content_id=model.content_id.value,
        version_id=model.version_id.value,
        version_number=int(model.version_number),
        content_type=model.content_type,
        title=model.title,
        description=model.description,
        locale=model.locale,
        artifact_status=model.artifact_status,
        origin=model.origin,
        aggregate_revision=int(model.aggregate_revision),
        submitted_at=model.submitted_at,
        version_created_at=model.version_created_at,
        published_version_id=(
            None
            if model.published_version_id is None
            else model.published_version_id.value
        ),
        schema_id=model.schema_id,
        schema_version=model.schema_version,
        payload=dict(model.payload),
        payload_sha256=model.payload_sha256,
    )


@router.get(
    "/teacher-os/review-queue",
    response_model=TeacherReviewQueueListResponse,
    operation_id="teacher_os_review_queue_list",
    responses=_LIST_RESPONSES,
    tags=["teacher-os"],
)
def teacher_os_review_queue_list(
    context: Annotated[TrustedSecurityContext, Depends(resolve_trusted_context)],
    service: Annotated[
        ListTeacherReviewQueueService, Depends(list_teacher_review_queue_service)
    ],
    codec: Annotated[CursorCodec, Depends(cursor_codec)],
    limit: Annotated[int | None, Query()] = None,
    cursor: Annotated[str | None, Query()] = None,
) -> TeacherReviewQueueListResponse:
    page_size = DEFAULT_LIST_LIMIT if limit is None else limit
    after_submitted_at = None
    after_content_id = None
    if cursor is not None:
        decoded = codec.decode_review_queue(
            cursor, expected_tenant_id=context.tenant_id
        )
        after_submitted_at = decoded.submitted_at
        after_content_id = ContentId(decoded.content_id)
    result = service.list(
        context.tenant_id,
        context.principal_id,
        ListTeacherReviewQueueQuery(
            limit=page_size,
            after_submitted_at=after_submitted_at,
            after_content_id=after_content_id,
        ),
    )
    items = [_to_queue_item(item) for item in result.items]
    next_cursor = None
    if result.has_more and result.items:
        last = result.items[-1]
        next_cursor = codec.encode_review_queue(
            ReviewQueueCursor(
                tenant_id=context.tenant_id,
                submitted_at=last.submitted_at,
                content_id=last.content_id.value,
            )
        )
    return TeacherReviewQueueListResponse(items=items, next_cursor=next_cursor)


@router.get(
    "/teacher-os/review-queue/{content_id}/versions/{version_id}",
    response_model=TeacherReviewQueueDetailResponse,
    operation_id="teacher_os_review_queue_get",
    responses=_GET_RESPONSES,
    tags=["teacher-os"],
)
def teacher_os_review_queue_get(
    content_id: UUID,
    version_id: UUID,
    response: Response,
    context: Annotated[TrustedSecurityContext, Depends(resolve_trusted_context)],
    service: Annotated[
        GetTeacherReviewQueueItemService, Depends(get_teacher_review_queue_item_service)
    ],
) -> TeacherReviewQueueDetailResponse:
    model = service.get(
        context.tenant_id,
        context.principal_id,
        _content_id(content_id),
        _version_id(version_id),
    )
    response.headers["ETag"] = encode_revision_etag(int(model.aggregate_revision))
    return _to_queue_detail(model)

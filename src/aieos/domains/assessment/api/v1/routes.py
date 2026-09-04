"""Assessment HTTP v1. Calls application services only."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response

from aieos.domains.assessment.api.v1.dependencies import (
    correct_classroom_assessment_service,
    get_classroom_assessment_service,
    list_classroom_assessments_service,
    record_classroom_assessment_service,
    resolve_trusted_context,
    void_classroom_assessment_service,
)
from aieos.domains.assessment.api.v1.models import (
    ClassroomAssessmentCorrectRequest,
    ClassroomAssessmentListResponse,
    ClassroomAssessmentRecordRequest,
    ClassroomAssessmentResponse,
)
from aieos.domains.assessment.application.audit import api_mutation_audit_provenance
from aieos.domains.assessment.application.models import (
    ClassroomAssessmentReadModel,
    CorrectClassroomAssessmentCommand,
    ListClassroomAssessmentsQuery,
    RecordClassroomAssessmentCommand,
)
from aieos.domains.assessment.application.mutations import (
    CorrectClassroomAssessmentService,
    VoidClassroomAssessmentService,
)
from aieos.domains.assessment.application.queries import (
    GetClassroomAssessmentService,
    ListClassroomAssessmentsService,
)
from aieos.domains.assessment.application.record import RecordClassroomAssessmentService
from aieos.domains.assessment.domain.identities import AggregateRevision, AssessmentId
from aieos.platform.api.etag import encode_revision_etag
from aieos.platform.api.idempotency_key import parse_idempotency_key
from aieos.platform.api.if_match import parse_if_match
from aieos.platform.api.problems import ProblemDetails
from aieos.platform.events.models import MutationEventContext
from aieos.platform.security.context import TrustedSecurityContext

DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 100

router = APIRouter(prefix="/api/v1", tags=["assessment"])


def _problem_responses(*statuses: int) -> dict[int, dict[str, object]]:
    return {
        status: {"model": ProblemDetails, "description": "RFC 9457 Problem Details"}
        for status in statuses
    }


_MUTATION_RESPONSES = _problem_responses(
    400, 401, 403, 404, 409, 412, 422, 428, 500, 503
)
_GET_RESPONSES = _problem_responses(400, 401, 403, 404, 422, 500)
_LIST_RESPONSES = _problem_responses(400, 401, 403, 422, 500)


def _to_response(model: ClassroomAssessmentReadModel) -> ClassroomAssessmentResponse:
    return ClassroomAssessmentResponse(
        assessment_id=model.assessment_id,
        teacher_principal_id=model.teacher_principal_id,
        class_ref=model.class_ref,
        content_id=model.content_id,
        content_version_id=model.content_version_id,
        class_result_level=model.class_result_level,
        class_result_note=model.class_result_note,
        lifecycle_state=model.lifecycle_state,
        work_id=model.work_id,
        execution_id=model.execution_id,
        assignment_id=model.assignment_id,
        aggregate_revision=model.aggregate_revision,
        recorded_at=model.recorded_at,
        voided_at=model.voided_at,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _mutation_event_context(
    request: Request, context: TrustedSecurityContext
) -> MutationEventContext:
    return MutationEventContext(
        correlation_id=request.state.correlation_id,
        causation_id=request.state.request_id,
        actor_principal_id=context.principal_id,
        effective_actor_id=context.principal_id,
    )


@router.post(
    "/assessment/classroom-assessments",
    response_model=ClassroomAssessmentResponse,
    status_code=201,
    operation_id="assessment_classroom_record",
    responses=_MUTATION_RESPONSES,
)
def assessment_classroom_record(
    body: ClassroomAssessmentRecordRequest,
    request: Request,
    response: Response,
    ctx: Annotated[TrustedSecurityContext, Depends(resolve_trusted_context)],
    service: Annotated[
        RecordClassroomAssessmentService, Depends(record_classroom_assessment_service)
    ],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ClassroomAssessmentResponse:
    key = parse_idempotency_key(idempotency_key)
    result = service.record(
        ctx.tenant_id,
        ctx.principal_id,
        RecordClassroomAssessmentCommand(
            class_ref=body.class_ref,
            content_id=body.content_id,
            content_version_id=body.content_version_id,
            class_result_level=body.class_result_level,
            class_result_note=body.class_result_note,
            work_id=body.work_id,
            execution_id=body.execution_id,
            assignment_id=body.assignment_id,
        ),
        idempotency_key=key,
        event_context=_mutation_event_context(request, ctx),
        audit_provenance=api_mutation_audit_provenance(ctx.principal_id),
    )
    response.headers["ETag"] = encode_revision_etag(result.aggregate_revision)
    return _to_response(result)


@router.get(
    "/assessment/classroom-assessments",
    response_model=ClassroomAssessmentListResponse,
    operation_id="assessment_classroom_list",
    responses=_LIST_RESPONSES,
)
def assessment_classroom_list(
    ctx: Annotated[TrustedSecurityContext, Depends(resolve_trusted_context)],
    service: Annotated[
        ListClassroomAssessmentsService, Depends(list_classroom_assessments_service)
    ],
    class_ref: Annotated[str | None, Query()] = None,
    work_id: Annotated[UUID | None, Query()] = None,
    execution_id: Annotated[UUID | None, Query()] = None,
    assignment_id: Annotated[UUID | None, Query()] = None,
    lifecycle_state: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIST_LIMIT)] = DEFAULT_LIST_LIMIT,
) -> ClassroomAssessmentListResponse:
    items = service.list(
        ctx.tenant_id,
        ctx.principal_id,
        ListClassroomAssessmentsQuery(
            class_ref=class_ref,
            work_id=work_id,
            execution_id=execution_id,
            assignment_id=assignment_id,
            lifecycle_state=lifecycle_state,
            limit=limit,
        ),
    )
    return ClassroomAssessmentListResponse(items=[_to_response(item) for item in items])


@router.get(
    "/assessment/classroom-assessments/{assessment_id}",
    response_model=ClassroomAssessmentResponse,
    operation_id="assessment_classroom_get",
    responses=_GET_RESPONSES,
)
def assessment_classroom_get(
    assessment_id: UUID,
    response: Response,
    ctx: Annotated[TrustedSecurityContext, Depends(resolve_trusted_context)],
    service: Annotated[
        GetClassroomAssessmentService, Depends(get_classroom_assessment_service)
    ],
) -> ClassroomAssessmentResponse:
    result = service.get(
        ctx.tenant_id, ctx.principal_id, AssessmentId(assessment_id)
    )
    response.headers["ETag"] = encode_revision_etag(result.aggregate_revision)
    return _to_response(result)


@router.post(
    "/assessment/classroom-assessments/{assessment_id}/actions/correct",
    response_model=ClassroomAssessmentResponse,
    operation_id="assessment_classroom_correct",
    responses=_MUTATION_RESPONSES,
)
def assessment_classroom_correct(
    assessment_id: UUID,
    body: ClassroomAssessmentCorrectRequest,
    request: Request,
    response: Response,
    ctx: Annotated[TrustedSecurityContext, Depends(resolve_trusted_context)],
    service: Annotated[
        CorrectClassroomAssessmentService,
        Depends(correct_classroom_assessment_service),
    ],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> ClassroomAssessmentResponse:
    key = parse_idempotency_key(idempotency_key)
    expected = AggregateRevision(parse_if_match(if_match))
    result = service.correct(
        ctx.tenant_id,
        ctx.principal_id,
        assessment_id=AssessmentId(assessment_id),
        expected_aggregate_revision=expected,
        command=CorrectClassroomAssessmentCommand(
            class_result_level=body.class_result_level,
            class_result_note=body.class_result_note,
        ),
        idempotency_key=key,
        event_context=_mutation_event_context(request, ctx),
        audit_provenance=api_mutation_audit_provenance(ctx.principal_id),
    )
    response.headers["ETag"] = encode_revision_etag(result.aggregate_revision)
    return _to_response(result)


@router.post(
    "/assessment/classroom-assessments/{assessment_id}/actions/void",
    response_model=ClassroomAssessmentResponse,
    operation_id="assessment_classroom_void",
    responses=_MUTATION_RESPONSES,
)
def assessment_classroom_void(
    assessment_id: UUID,
    request: Request,
    response: Response,
    ctx: Annotated[TrustedSecurityContext, Depends(resolve_trusted_context)],
    service: Annotated[
        VoidClassroomAssessmentService, Depends(void_classroom_assessment_service)
    ],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> ClassroomAssessmentResponse:
    key = parse_idempotency_key(idempotency_key)
    expected = AggregateRevision(parse_if_match(if_match))
    result = service.void(
        ctx.tenant_id,
        ctx.principal_id,
        assessment_id=AssessmentId(assessment_id),
        expected_aggregate_revision=expected,
        idempotency_key=key,
        event_context=_mutation_event_context(request, ctx),
        audit_provenance=api_mutation_audit_provenance(ctx.principal_id),
    )
    response.headers["ETag"] = encode_revision_etag(result.aggregate_revision)
    return _to_response(result)

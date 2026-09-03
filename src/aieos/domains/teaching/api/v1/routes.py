"""Teaching HTTP v1. Calls application services only."""

from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response

from aieos.domains.teaching.api.v1.dependencies import (
    cancel_teaching_assignment_service,
    cancel_teaching_execution_service,
    close_teaching_assignment_service,
    complete_teaching_execution_service,
    correct_teaching_execution_observation_service,
    create_teaching_assignment_service,
    create_teaching_execution_observation_service,
    create_teaching_work_service,
    generate_teaching_work_service,
    get_teaching_assignment_service,
    get_teaching_execution_service,
    get_teaching_work_service,
    list_assignable_school_classes_service,
    list_teaching_assignment_service,
    list_teaching_executions_service,
    list_teaching_work_artifacts_service,
    list_teaching_works_service,
    prepare_teaching_work_service,
    refine_teaching_work_service,
    resolve_trusted_context,
    start_teaching_execution_service,
    teacher_os_teach_context_service,
    teacher_os_today_mission_service,
    update_teaching_assignment_due_service,
)
from aieos.domains.teaching.api.v1.models import (
    ContinueWorkSummaryResponse,
    EducationalQualityCheckResponse,
    EducationalQualityResponse,
    GeneratedArtifactResponse,
    HeroActionResponse,
    PreparationArtifactResponse,
    PreparationProjectionResponse,
    PreparationStatusResponse,
    ReviewProjectionResponse,
    SchoolContextClassItemResponse,
    SchoolContextClassesResponse,
    TeacherOsMissionResponse,
    TeacherOsTeachContextResponse,
    TeachingWorkArtifactsResponse,
    TeachingWorkCreateRequest,
    TeachingWorkGenerateResponse,
    TeachingWorkListResponse,
    TeachingWorkPrepareResponse,
    TeachingWorkRefineRequest,
    TeachingWorkResponse,
    TeachingAssignmentCreateRequest,
    TeachingAssignmentDueUpdateRequest,
    TeachingAssignmentListResponse,
    TeachingAssignmentResponse,
    TeachingExecutionContentBindingResponse,
    TeachingExecutionListResponse,
    TeachingExecutionObservationCorrectRequest,
    TeachingExecutionObservationCreateRequest,
    TeachingExecutionObservationResponse,
    TeachingExecutionResponse,
    TeachingExecutionStartRequest,
    WorkArtifactItemResponse,
)
from aieos.domains.teaching.application.assignment_create import (
    CreateTeachingAssignmentService,
)
from aieos.domains.teaching.application.assignment_mutations import (
    CancelTeachingAssignmentService,
    CloseTeachingAssignmentService,
    UpdateTeachingAssignmentDueService,
)
from aieos.domains.teaching.application.assignment_queries import (
    GetTeachingAssignmentService,
    ListTeachingAssignmentsService,
)
from aieos.domains.teaching.application.artifacts import ListTeachingWorkArtifactsService
from aieos.domains.teaching.application.create import CreateTeachingWorkService
from aieos.domains.teaching.application.errors import (
    InvalidTeachingExecutionRequest,
    InvalidTeachingWorkRequest,
    PreparationRecoveryInvariantError,
)
from aieos.domains.teaching.application.execution_mutations import (
    CancelTeachingExecutionService,
    CompleteTeachingExecutionService,
)
from aieos.domains.teaching.application.execution_observations import (
    CorrectTeachingExecutionObservationService,
    CreateTeachingExecutionObservationService,
)
from aieos.domains.teaching.application.execution_queries import (
    GetTeachingExecutionService,
    ListTeachingExecutionsService,
)
from aieos.domains.teaching.application.execution_start import (
    StartTeachingExecutionService,
)
from aieos.domains.teaching.application.generate import (
    GenerateTeachingWorkResult,
    GenerateTeachingWorkService,
)
from aieos.domains.teaching.application.mission import GetTeacherOsTodayMissionService
from aieos.domains.teaching.application.mission_models import TeacherOsMission
from aieos.domains.teaching.application.audit import api_mutation_audit_provenance
from aieos.domains.teaching.application.models import (
    CorrectTeachingExecutionObservationCommand,
    CreateTeachingAssignmentCommand,
    CreateTeachingExecutionObservationCommand,
    CreateTeachingWorkCommand,
    ListTeachingAssignmentsQuery,
    ListTeachingExecutionsQuery,
    ListTeachingWorksQuery,
    RefineTeachingWorkCommand,
    StartTeachingExecutionCommand,
    TeachingAssignmentReadModel,
    TeachingExecutionContentBindingInput,
    TeachingExecutionObservationReadModel,
    TeachingExecutionReadModel,
    TeachingWorkReadModel,
    UpdateTeachingAssignmentDueCommand,
    GetTeacherOsTeachContextQuery,
)
from aieos.domains.teaching.application.prepare import (
    PrepareTeachingWorkResult,
    PrepareTeachingWorkService,
)
from aieos.domains.teaching.application.queries import (
    GetTeachingWorkService,
    ListTeachingWorksService,
)
from aieos.domains.teaching.application.refine import RefineTeachingWorkService
from aieos.domains.teaching.application.school_context import (
    ListAssignableSchoolClassesService,
)
from aieos.domains.teaching.application.teach_composition import (
    GetTeacherOsTeachContextService,
    TeacherOsTeachContextReadModel,
)
from aieos.domains.education.schema import PREPARATION_ARTIFACT_KINDS
from aieos.domains.teaching.domain.errors import InvalidTeachingIdentityError
from aieos.domains.teaching.domain.identities import (
    AggregateRevision,
    AssignmentId,
    ExecutionId,
    ObservationId,
    ObservationRevision,
    WorkId,
)
from aieos.domains.teaching.domain.work import UNSET
from aieos.platform.api.etag import encode_revision_etag
from aieos.platform.api.idempotency_key import parse_idempotency_key
from aieos.platform.api.if_match import parse_if_match
from aieos.platform.api.problems import ProblemDetails
from aieos.platform.events.models import MutationEventContext
from aieos.platform.security.context import TrustedSecurityContext

DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 100

router = APIRouter(prefix="/api/v1", tags=["teaching"])


def _problem_responses(*statuses: int) -> dict[int, dict[str, object]]:
    return {
        status: {"model": ProblemDetails, "description": "RFC 9457 Problem Details"}
        for status in statuses
    }


_CREATE_RESPONSES = _problem_responses(400, 401, 403, 409, 422, 500, 503)
_GET_RESPONSES = _problem_responses(400, 401, 403, 404, 422, 500, 503)
_LIST_RESPONSES = _problem_responses(400, 401, 403, 422, 500, 503)
_REFINE_RESPONSES = _problem_responses(
    400, 401, 403, 404, 409, 412, 422, 428, 500, 503
)
_GENERATE_RESPONSES = _problem_responses(
    400, 401, 403, 404, 409, 412, 422, 428, 500, 502, 503
)
_PREPARE_RESPONSES = _problem_responses(
    400, 401, 403, 404, 409, 412, 422, 428, 500, 502, 503
)
_ASSIGNMENT_CREATE_RESPONSES = _problem_responses(
    400, 401, 403, 409, 422, 500, 503
)
_ASSIGNMENT_MUTATION_RESPONSES = _problem_responses(
    400, 401, 403, 404, 409, 412, 422, 428, 500, 503
)
_EXECUTION_CREATE_RESPONSES = _problem_responses(
    400, 401, 403, 409, 422, 500, 503
)
_EXECUTION_MUTATION_RESPONSES = _problem_responses(
    400, 401, 403, 404, 409, 412, 422, 428, 500, 503
)
_OBSERVATION_CREATE_RESPONSES = _problem_responses(
    400, 401, 403, 404, 409, 422, 500, 503
)
_OBSERVATION_CORRECT_RESPONSES = _problem_responses(
    400, 401, 403, 404, 409, 412, 422, 428, 500, 503
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


def _work_id(value: UUID) -> WorkId:
    try:
        return WorkId(value)
    except InvalidTeachingIdentityError as exc:
        raise InvalidTeachingWorkRequest("work_id must be a UUIDv7") from exc


def _assignment_id(value: UUID) -> AssignmentId:
    try:
        return AssignmentId(value)
    except InvalidTeachingIdentityError as exc:
        raise InvalidTeachingWorkRequest("assignment_id must be a UUIDv7") from exc


def _execution_id(value: UUID) -> ExecutionId:
    try:
        return ExecutionId(value)
    except InvalidTeachingIdentityError as exc:
        raise InvalidTeachingExecutionRequest("execution_id must be a UUIDv7") from exc


def _observation_id(value: UUID) -> ObservationId:
    try:
        return ObservationId(value)
    except InvalidTeachingIdentityError as exc:
        raise InvalidTeachingExecutionRequest(
            "observation_id must be a UUIDv7"
        ) from exc


def _to_observation_response(
    model: TeachingExecutionObservationReadModel,
) -> TeachingExecutionObservationResponse:
    return TeachingExecutionObservationResponse(
        observation_id=model.observation_id.value,
        execution_id=model.execution_id.value,
        observation_kind=model.observation_kind,
        body=model.body,
        recorded_at=model.recorded_at,
        updated_at=model.updated_at,
        revision=int(model.revision),
    )


def _to_execution_response(
    model: TeachingExecutionReadModel,
) -> TeachingExecutionResponse:
    return TeachingExecutionResponse(
        execution_id=model.execution_id.value,
        teacher_principal_id=model.teacher_principal_id,
        work_id=model.work_id.value,
        class_ref=model.class_ref,
        lifecycle_state=model.lifecycle_state,
        started_at=model.started_at,
        completed_at=model.completed_at,
        cancelled_at=model.cancelled_at,
        aggregate_revision=int(model.aggregate_revision),
        created_at=model.created_at,
        updated_at=model.updated_at,
        bindings=[
            TeachingExecutionContentBindingResponse(
                content_id=binding.content_id,
                content_version_id=binding.content_version_id,
                artifact_kind=binding.artifact_kind,
            )
            for binding in model.bindings
        ],
        observations=[
            _to_observation_response(item) for item in model.observations
        ],
    )


def _to_artifact_item_response(item) -> WorkArtifactItemResponse:
    return WorkArtifactItemResponse(
        content_id=item.content_id,
        version_id=item.version_id,
        content_type=item.content_type,
        title=item.title,
        origin=item.origin,
        stewardship_state=item.stewardship_state,
        aggregate_revision=item.aggregate_revision,
        educational_quality=(
            None
            if item.educational_quality is None
            else EducationalQualityResponse(
                status=item.educational_quality.status,
                checks=[
                    EducationalQualityCheckResponse(
                        code=str(check["code"]),
                        passed=bool(check["passed"]),
                        explanation=str(check["explanation"]),
                    )
                    for check in item.educational_quality.checks
                ],
            )
        ),
        artifact_kind=item.artifact_kind,
        generation_run_id=item.generation_run_id,
    )


def _to_teach_context_response(
    model: TeacherOsTeachContextReadModel,
) -> TeacherOsTeachContextResponse:
    work = model.work
    return TeacherOsTeachContextResponse(
        work=ContinueWorkSummaryResponse(
            work_id=work.work_id.value,
            intent_type=work.intent_type,
            goal_text=work.goal_text,
            class_label=work.class_label,
            subject=work.subject,
            topic=work.topic,
            target_date=work.target_date,
            aggregate_revision=int(work.aggregate_revision),
            updated_at=work.updated_at,
        ),
        class_ref=model.class_ref,
        display_label=model.class_display_label,
        artifacts=[_to_artifact_item_response(item) for item in model.artifacts.items],
        assignments=[_to_assignment_response(item) for item in model.assignments],
        executions=[_to_execution_response(item) for item in model.executions],
    )


def _to_assignment_response(
    model: TeachingAssignmentReadModel,
) -> TeachingAssignmentResponse:
    return TeachingAssignmentResponse(
        assignment_id=model.assignment_id.value,
        teacher_principal_id=model.teacher_principal_id,
        content_id=model.content_id,
        content_version_id=model.content_version_id,
        audience_type=model.audience_type,
        class_ref=model.class_ref,
        audience_display_label=model.audience_display_label,
        source_work_id=(
            None
            if model.source_work_id is None
            else model.source_work_id.value
        ),
        lifecycle_state=model.lifecycle_state,
        assigned_at=model.assigned_at,
        available_from=model.available_from,
        due_at=model.due_at,
        closed_at=model.closed_at,
        cancelled_at=model.cancelled_at,
        aggregate_revision=int(model.aggregate_revision),
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _to_response(model: TeachingWorkReadModel) -> TeachingWorkResponse:
    return TeachingWorkResponse(
        work_id=model.work_id.value,
        intent_type=model.intent_type,
        goal_text=model.goal_text,
        class_label=model.class_label,
        subject=model.subject,
        topic=model.topic,
        target_date=model.target_date,
        locale=model.locale,
        aggregate_revision=int(model.aggregate_revision),
        created_at=model.created_at,
        updated_at=model.updated_at,
        archived_at=model.archived_at,
    )


def _to_generate_response(result: GenerateTeachingWorkResult) -> TeachingWorkGenerateResponse:
    return TeachingWorkGenerateResponse(
        work_id=result.work_id.value,
        generation_run_id=result.generation_run_id.value,
        artifact=GeneratedArtifactResponse(
            content_id=result.artifact.content_id,
            version_id=result.artifact.version_id,
            content_type=result.artifact.content_type,
            title=result.artifact.title,
            stewardship_state=result.artifact.stewardship_state,
            aggregate_revision=result.artifact.aggregate_revision,
        ),
        educational_quality=EducationalQualityResponse(
            status=result.educational_quality.status,
            checks=[
                EducationalQualityCheckResponse(
                    code=str(check["code"]),
                    passed=bool(check["passed"]),
                    explanation=str(check["explanation"]),
                )
                for check in result.educational_quality.checks
            ],
        ),
    )


def _to_prepare_response(result: PrepareTeachingWorkResult) -> TeachingWorkPrepareResponse:
    kinds = tuple(item.artifact_kind for item in result.artifacts)
    if kinds != PREPARATION_ARTIFACT_KINDS:
        raise PreparationRecoveryInvariantError(
            "preparation response must contain exact six canonical artifacts"
        )
    run_id = result.generation_run_id.value
    if any(item.content_id is None for item in result.artifacts):
        raise PreparationRecoveryInvariantError(
            "preparation response artifacts must include Content identity"
        )
    return TeachingWorkPrepareResponse(
        work_id=result.work_id.value,
        generation_run_id=run_id,
        preparation=PreparationStatusResponse(status="ready"),
        artifacts=[
            PreparationArtifactResponse(
                artifact_kind=item.artifact_kind,
                content_id=item.content_id,
                version_id=item.version_id,
                content_type=item.content_type,
                title=item.title,
                stewardship_state=item.stewardship_state,
                aggregate_revision=item.aggregate_revision,
                generation_run_id=run_id,
            )
            for item in result.artifacts
        ],
        educational_quality=EducationalQualityResponse(
            status=result.educational_quality.status,
            checks=[
                EducationalQualityCheckResponse(
                    code=str(check["code"]),
                    passed=bool(check["passed"]),
                    explanation=str(check["explanation"]),
                )
                for check in result.educational_quality.checks
            ],
        ),
    )


def _to_mission_response(mission: TeacherOsMission) -> TeacherOsMissionResponse:
    continue_work = mission.preparation.continue_work
    return TeacherOsMissionResponse(
        mission_date=mission.mission_date,
        review=ReviewProjectionResponse(pending_count=mission.review.pending_count),
        preparation=PreparationProjectionResponse(
            active_work_count=mission.preparation.active_work_count,
            continue_work=(
                None
                if continue_work is None
                else ContinueWorkSummaryResponse(
                    work_id=continue_work.work_id.value,
                    intent_type=continue_work.intent_type,
                    goal_text=continue_work.goal_text,
                    class_label=continue_work.class_label,
                    subject=continue_work.subject,
                    topic=continue_work.topic,
                    target_date=continue_work.target_date,
                    aggregate_revision=continue_work.aggregate_revision,
                    updated_at=continue_work.updated_at,
                )
            ),
        ),
        hero_action=HeroActionResponse(
            kind=mission.hero_action.kind.value,
            work_id=(
                None
                if mission.hero_action.work_id is None
                else mission.hero_action.work_id.value
            ),
        ),
    )


def _refine_command(body: TeachingWorkRefineRequest) -> RefineTeachingWorkCommand:
    """Build PATCH semantics from the request body.

    Only fields present in the JSON body are refined. goal_text, target_date,
    and locale are non-nullable, so an explicit null for them is rejected.
    """
    provided = body.model_fields_set
    for required_field in ("goal_text", "target_date", "locale"):
        if required_field in provided and getattr(body, required_field) is None:
            raise InvalidTeachingWorkRequest(f"{required_field} must not be null")
    return RefineTeachingWorkCommand(
        goal_text=body.goal_text if "goal_text" in provided else UNSET,
        class_label=body.class_label if "class_label" in provided else UNSET,
        subject=body.subject if "subject" in provided else UNSET,
        topic=body.topic if "topic" in provided else UNSET,
        target_date=body.target_date if "target_date" in provided else UNSET,
        locale=body.locale if "locale" in provided else UNSET,
    )


@router.post(
    "/teaching/works",
    status_code=201,
    response_model=TeachingWorkResponse,
    operation_id="teaching_work_create",
    responses=_CREATE_RESPONSES,
)
def teaching_work_create(
    body: TeachingWorkCreateRequest,
    response: Response,
    context: Annotated[TrustedSecurityContext, Depends(resolve_trusted_context)],
    service: Annotated[CreateTeachingWorkService, Depends(create_teaching_work_service)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> TeachingWorkResponse:
    key = parse_idempotency_key(idempotency_key)
    model = service.create(
        context.tenant_id,
        context.principal_id,
        CreateTeachingWorkCommand(
            intent_type=body.intent_type,
            goal_text=body.goal_text,
            target_date=body.target_date,
            locale=body.locale,
            class_label=body.class_label,
            subject=body.subject,
            topic=body.topic,
        ),
        idempotency_key=key,
    )
    response.headers["Location"] = f"/api/v1/teaching/works/{model.work_id}"
    response.headers["ETag"] = encode_revision_etag(int(model.aggregate_revision))
    return _to_response(model)


@router.get(
    "/teaching/works",
    response_model=TeachingWorkListResponse,
    operation_id="teaching_work_list",
    responses=_LIST_RESPONSES,
)
def teaching_work_list(
    context: Annotated[TrustedSecurityContext, Depends(resolve_trusted_context)],
    service: Annotated[ListTeachingWorksService, Depends(list_teaching_works_service)],
    limit: Annotated[int | None, Query(ge=1)] = None,
    include_archived: Annotated[bool, Query()] = False,
) -> TeachingWorkListResponse:
    if limit is not None and limit > MAX_LIST_LIMIT:
        raise InvalidTeachingWorkRequest("list limit exceeds the maximum of 100")
    page_size = DEFAULT_LIST_LIMIT if limit is None else limit
    result = service.list(
        context.tenant_id,
        context.principal_id,
        ListTeachingWorksQuery(limit=page_size, include_archived=include_archived),
    )
    return TeachingWorkListResponse(
        items=[_to_response(item) for item in result.items],
        has_more=result.has_more,
    )


@router.get(
    "/teaching/works/{work_id}",
    response_model=TeachingWorkResponse,
    operation_id="teaching_work_get",
    responses=_GET_RESPONSES,
)
def teaching_work_get(
    work_id: UUID,
    response: Response,
    context: Annotated[TrustedSecurityContext, Depends(resolve_trusted_context)],
    service: Annotated[GetTeachingWorkService, Depends(get_teaching_work_service)],
) -> TeachingWorkResponse:
    model = service.get(context.tenant_id, context.principal_id, _work_id(work_id))
    response.headers["ETag"] = encode_revision_etag(int(model.aggregate_revision))
    return _to_response(model)


@router.patch(
    "/teaching/works/{work_id}",
    response_model=TeachingWorkResponse,
    operation_id="teaching_work_refine",
    responses=_REFINE_RESPONSES,
)
def teaching_work_refine(
    work_id: UUID,
    body: TeachingWorkRefineRequest,
    response: Response,
    context: Annotated[TrustedSecurityContext, Depends(resolve_trusted_context)],
    service: Annotated[RefineTeachingWorkService, Depends(refine_teaching_work_service)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> TeachingWorkResponse:
    key = parse_idempotency_key(idempotency_key)
    expected = AggregateRevision(parse_if_match(if_match))
    model = service.refine(
        context.tenant_id,
        context.principal_id,
        work_id=_work_id(work_id),
        expected_aggregate_revision=expected,
        command=_refine_command(body),
        idempotency_key=key,
    )
    response.headers["ETag"] = encode_revision_etag(int(model.aggregate_revision))
    return _to_response(model)


@router.post(
    "/teaching/works/{work_id}/actions/generate",
    response_model=TeachingWorkGenerateResponse,
    operation_id="teaching_work_generate",
    responses=_GENERATE_RESPONSES,
)
def teaching_work_generate(
    work_id: UUID,
    request: Request,
    context: Annotated[TrustedSecurityContext, Depends(resolve_trusted_context)],
    service: Annotated[
        GenerateTeachingWorkService, Depends(generate_teaching_work_service)
    ],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> TeachingWorkGenerateResponse:
    key = parse_idempotency_key(idempotency_key)
    expected = AggregateRevision(parse_if_match(if_match))
    result = service.generate(
        context.tenant_id,
        context.principal_id,
        work_id=_work_id(work_id),
        expected_aggregate_revision=expected,
        idempotency_key=key,
        event_context=_mutation_event_context(request, context),
    )
    return _to_generate_response(result)


@router.post(
    "/teaching/works/{work_id}/actions/prepare",
    response_model=TeachingWorkPrepareResponse,
    operation_id="teaching_work_prepare",
    responses=_PREPARE_RESPONSES,
)
def teaching_work_prepare(
    work_id: UUID,
    request: Request,
    context: Annotated[TrustedSecurityContext, Depends(resolve_trusted_context)],
    service: Annotated[
        PrepareTeachingWorkService, Depends(prepare_teaching_work_service)
    ],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> TeachingWorkPrepareResponse:
    key = parse_idempotency_key(idempotency_key)
    expected = AggregateRevision(parse_if_match(if_match))
    result = service.prepare(
        context.tenant_id,
        context.principal_id,
        work_id=_work_id(work_id),
        expected_aggregate_revision=expected,
        idempotency_key=key,
        event_context=_mutation_event_context(request, context),
    )
    return _to_prepare_response(result)


@router.get(
    "/teaching/works/{work_id}/artifacts",
    response_model=TeachingWorkArtifactsResponse,
    operation_id="teaching_work_artifacts_list",
    responses=_GET_RESPONSES,
)
def teaching_work_artifacts_list(
    work_id: UUID,
    context: Annotated[TrustedSecurityContext, Depends(resolve_trusted_context)],
    service: Annotated[
        ListTeachingWorkArtifactsService, Depends(list_teaching_work_artifacts_service)
    ],
) -> TeachingWorkArtifactsResponse:
    result = service.list(context.tenant_id, context.principal_id, _work_id(work_id))
    return TeachingWorkArtifactsResponse(
        work_id=result.work_id.value,
        items=[_to_artifact_item_response(item) for item in result.items],
    )


@router.post(
    "/teaching/assignments",
    status_code=201,
    response_model=TeachingAssignmentResponse,
    operation_id="teaching_assignment_create",
    responses=_ASSIGNMENT_CREATE_RESPONSES,
)
def teaching_assignment_create(
    body: TeachingAssignmentCreateRequest,
    request: Request,
    response: Response,
    context: Annotated[TrustedSecurityContext, Depends(resolve_trusted_context)],
    service: Annotated[
        CreateTeachingAssignmentService, Depends(create_teaching_assignment_service)
    ],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> TeachingAssignmentResponse:
    key = parse_idempotency_key(idempotency_key)
    model = service.create(
        context.tenant_id,
        context.principal_id,
        CreateTeachingAssignmentCommand(
            content_id=body.content_id,
            content_version_id=body.content_version_id,
            class_ref=body.class_ref,
            source_work_id=body.source_work_id,
            available_from=body.available_from,
            due_at=body.due_at,
        ),
        idempotency_key=key,
        event_context=_mutation_event_context(request, context),
        audit_provenance=api_mutation_audit_provenance(context.principal_id),
    )
    response.headers["Location"] = (
        f"/api/v1/teaching/assignments/{model.assignment_id.value}"
    )
    response.headers["ETag"] = encode_revision_etag(int(model.aggregate_revision))
    return _to_assignment_response(model)


@router.get(
    "/teaching/assignments",
    response_model=TeachingAssignmentListResponse,
    operation_id="teaching_assignment_list",
    responses=_LIST_RESPONSES,
)
def teaching_assignment_list(
    context: Annotated[TrustedSecurityContext, Depends(resolve_trusted_context)],
    service: Annotated[
        ListTeachingAssignmentsService, Depends(list_teaching_assignment_service)
    ],
    limit: Annotated[int | None, Query(ge=1)] = None,
    lifecycle_state: Annotated[str | None, Query()] = None,
) -> TeachingAssignmentListResponse:
    if limit is not None and limit > MAX_LIST_LIMIT:
        raise InvalidTeachingWorkRequest("list limit exceeds the maximum of 100")
    page_size = DEFAULT_LIST_LIMIT if limit is None else limit
    result = service.list(
        context.tenant_id,
        context.principal_id,
        ListTeachingAssignmentsQuery(
            limit=page_size, lifecycle_state=lifecycle_state
        ),
    )
    return TeachingAssignmentListResponse(
        items=[_to_assignment_response(item) for item in result.items],
        has_more=result.has_more,
    )


@router.get(
    "/teaching/assignments/{assignment_id}",
    response_model=TeachingAssignmentResponse,
    operation_id="teaching_assignment_get",
    responses=_GET_RESPONSES,
)
def teaching_assignment_get(
    assignment_id: UUID,
    response: Response,
    context: Annotated[TrustedSecurityContext, Depends(resolve_trusted_context)],
    service: Annotated[
        GetTeachingAssignmentService, Depends(get_teaching_assignment_service)
    ],
) -> TeachingAssignmentResponse:
    model = service.get(
        context.tenant_id,
        context.principal_id,
        _assignment_id(assignment_id),
    )
    response.headers["ETag"] = encode_revision_etag(int(model.aggregate_revision))
    return _to_assignment_response(model)


@router.patch(
    "/teaching/assignments/{assignment_id}",
    response_model=TeachingAssignmentResponse,
    operation_id="teaching_assignment_due_update",
    responses=_ASSIGNMENT_MUTATION_RESPONSES,
)
def teaching_assignment_due_update(
    assignment_id: UUID,
    body: TeachingAssignmentDueUpdateRequest,
    request: Request,
    response: Response,
    context: Annotated[TrustedSecurityContext, Depends(resolve_trusted_context)],
    service: Annotated[
        UpdateTeachingAssignmentDueService,
        Depends(update_teaching_assignment_due_service),
    ],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> TeachingAssignmentResponse:
    key = parse_idempotency_key(idempotency_key)
    expected = AggregateRevision(parse_if_match(if_match))
    model = service.update_due(
        context.tenant_id,
        context.principal_id,
        assignment_id=_assignment_id(assignment_id),
        expected_aggregate_revision=expected,
        command=UpdateTeachingAssignmentDueCommand(due_at=body.due_at),
        idempotency_key=key,
        event_context=_mutation_event_context(request, context),
        audit_provenance=api_mutation_audit_provenance(context.principal_id),
    )
    response.headers["ETag"] = encode_revision_etag(int(model.aggregate_revision))
    return _to_assignment_response(model)


@router.post(
    "/teaching/assignments/{assignment_id}/actions/close",
    response_model=TeachingAssignmentResponse,
    operation_id="teaching_assignment_close",
    responses=_ASSIGNMENT_MUTATION_RESPONSES,
)
def teaching_assignment_close(
    assignment_id: UUID,
    request: Request,
    response: Response,
    context: Annotated[TrustedSecurityContext, Depends(resolve_trusted_context)],
    service: Annotated[
        CloseTeachingAssignmentService, Depends(close_teaching_assignment_service)
    ],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> TeachingAssignmentResponse:
    key = parse_idempotency_key(idempotency_key)
    expected = AggregateRevision(parse_if_match(if_match))
    model = service.close(
        context.tenant_id,
        context.principal_id,
        assignment_id=_assignment_id(assignment_id),
        expected_aggregate_revision=expected,
        idempotency_key=key,
        event_context=_mutation_event_context(request, context),
        audit_provenance=api_mutation_audit_provenance(context.principal_id),
    )
    response.headers["ETag"] = encode_revision_etag(int(model.aggregate_revision))
    return _to_assignment_response(model)


@router.post(
    "/teaching/assignments/{assignment_id}/actions/cancel",
    response_model=TeachingAssignmentResponse,
    operation_id="teaching_assignment_cancel",
    responses=_ASSIGNMENT_MUTATION_RESPONSES,
)
def teaching_assignment_cancel(
    assignment_id: UUID,
    request: Request,
    response: Response,
    context: Annotated[TrustedSecurityContext, Depends(resolve_trusted_context)],
    service: Annotated[
        CancelTeachingAssignmentService, Depends(cancel_teaching_assignment_service)
    ],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> TeachingAssignmentResponse:
    key = parse_idempotency_key(idempotency_key)
    expected = AggregateRevision(parse_if_match(if_match))
    model = service.cancel(
        context.tenant_id,
        context.principal_id,
        assignment_id=_assignment_id(assignment_id),
        expected_aggregate_revision=expected,
        idempotency_key=key,
        event_context=_mutation_event_context(request, context),
        audit_provenance=api_mutation_audit_provenance(context.principal_id),
    )
    response.headers["ETag"] = encode_revision_etag(int(model.aggregate_revision))
    return _to_assignment_response(model)


@router.get(
    "/teacher-os/today/mission",
    response_model=TeacherOsMissionResponse,
    operation_id="teacher_os_today_mission",
    responses=_GET_RESPONSES,
    tags=["teacher-os"],
)
def teacher_os_today_mission(
    context: Annotated[TrustedSecurityContext, Depends(resolve_trusted_context)],
    service: Annotated[
        GetTeacherOsTodayMissionService, Depends(teacher_os_today_mission_service)
    ],
    mission_date: Annotated[
        date,
        Query(
            description=(
                "Local educational day as YYYY-MM-DD. TOS-DEV02 temporary "
                "contract: the client supplies the date because no teacher "
                "time-zone System of Record exists yet."
            )
        ),
    ],
) -> TeacherOsMissionResponse:
    mission = service.get(
        context.tenant_id,
        context.principal_id,
        mission_date=mission_date,
    )
    return _to_mission_response(mission)


@router.get(
    "/teacher-os/school-context/classes",
    response_model=SchoolContextClassesResponse,
    operation_id="teacher_os_school_context_classes_list",
    responses=_GET_RESPONSES,
    tags=["teacher-os"],
)
def teacher_os_school_context_classes_list(
    context: Annotated[TrustedSecurityContext, Depends(resolve_trusted_context)],
    service: Annotated[
        ListAssignableSchoolClassesService,
        Depends(list_assignable_school_classes_service),
    ],
) -> SchoolContextClassesResponse:
    """Current-authority assignable ClassRef list for Teacher OS Assign UX.

    Advisory read only. Does not authorize TeachingAssignment CREATE.
    Tenant and teacher Principal come only from resolve_trusted_context.
    """
    items = service.list(context.tenant_id, context.principal_id)
    return SchoolContextClassesResponse(
        items=[
            SchoolContextClassItemResponse(
                class_ref=item.class_ref,
                display_label=item.display_label,
            )
            for item in items
        ]
    )


@router.post(
    "/teaching/executions",
    status_code=201,
    response_model=TeachingExecutionResponse,
    operation_id="teaching_execution_start",
    responses=_EXECUTION_CREATE_RESPONSES,
)
def teaching_execution_start(
    body: TeachingExecutionStartRequest,
    request: Request,
    response: Response,
    context: Annotated[TrustedSecurityContext, Depends(resolve_trusted_context)],
    service: Annotated[
        StartTeachingExecutionService, Depends(start_teaching_execution_service)
    ],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> TeachingExecutionResponse:
    key = parse_idempotency_key(idempotency_key)
    model = service.start(
        context.tenant_id,
        context.principal_id,
        StartTeachingExecutionCommand(
            work_id=body.work_id,
            class_ref=body.class_ref,
            bindings=tuple(
                TeachingExecutionContentBindingInput(
                    content_id=binding.content_id,
                    content_version_id=binding.content_version_id,
                    artifact_kind=binding.artifact_kind,
                )
                for binding in body.bindings
            ),
        ),
        idempotency_key=key,
        event_context=_mutation_event_context(request, context),
        audit_provenance=api_mutation_audit_provenance(context.principal_id),
    )
    response.headers["Location"] = (
        f"/api/v1/teaching/executions/{model.execution_id.value}"
    )
    response.headers["ETag"] = encode_revision_etag(int(model.aggregate_revision))
    return _to_execution_response(model)


@router.get(
    "/teaching/executions",
    response_model=TeachingExecutionListResponse,
    operation_id="teaching_execution_list",
    responses=_LIST_RESPONSES,
)
def teaching_execution_list(
    context: Annotated[TrustedSecurityContext, Depends(resolve_trusted_context)],
    service: Annotated[
        ListTeachingExecutionsService, Depends(list_teaching_executions_service)
    ],
    work_id: Annotated[UUID | None, Query()] = None,
    class_ref: Annotated[str | None, Query()] = None,
    lifecycle_state: Annotated[str | None, Query()] = None,
    limit: Annotated[int | None, Query(ge=1)] = None,
) -> TeachingExecutionListResponse:
    if limit is not None and limit > MAX_LIST_LIMIT:
        raise InvalidTeachingExecutionRequest("list limit exceeds the maximum of 100")
    page_size = DEFAULT_LIST_LIMIT if limit is None else limit
    result = service.list(
        context.tenant_id,
        context.principal_id,
        ListTeachingExecutionsQuery(
            limit=page_size,
            work_id=work_id,
            class_ref=class_ref,
            lifecycle_state=lifecycle_state,
        ),
    )
    return TeachingExecutionListResponse(
        items=[_to_execution_response(item) for item in result.items],
        has_more=result.has_more,
    )


@router.get(
    "/teaching/executions/{execution_id}",
    response_model=TeachingExecutionResponse,
    operation_id="teaching_execution_get",
    responses=_GET_RESPONSES,
)
def teaching_execution_get(
    execution_id: UUID,
    response: Response,
    context: Annotated[TrustedSecurityContext, Depends(resolve_trusted_context)],
    service: Annotated[
        GetTeachingExecutionService, Depends(get_teaching_execution_service)
    ],
) -> TeachingExecutionResponse:
    model = service.get(
        context.tenant_id,
        context.principal_id,
        _execution_id(execution_id),
    )
    response.headers["ETag"] = encode_revision_etag(int(model.aggregate_revision))
    return _to_execution_response(model)


@router.post(
    "/teaching/executions/{execution_id}/observations",
    status_code=201,
    response_model=TeachingExecutionObservationResponse,
    operation_id="teaching_execution_observation_create",
    responses=_OBSERVATION_CREATE_RESPONSES,
)
def teaching_execution_observation_create(
    execution_id: UUID,
    body: TeachingExecutionObservationCreateRequest,
    request: Request,
    response: Response,
    context: Annotated[TrustedSecurityContext, Depends(resolve_trusted_context)],
    service: Annotated[
        CreateTeachingExecutionObservationService,
        Depends(create_teaching_execution_observation_service),
    ],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> TeachingExecutionObservationResponse:
    key = parse_idempotency_key(idempotency_key)
    model = service.create(
        context.tenant_id,
        context.principal_id,
        execution_id=_execution_id(execution_id),
        command=CreateTeachingExecutionObservationCommand(
            observation_kind=body.observation_kind,
            body=body.body,
        ),
        idempotency_key=key,
        event_context=_mutation_event_context(request, context),
        audit_provenance=api_mutation_audit_provenance(context.principal_id),
    )
    response.headers["ETag"] = encode_revision_etag(int(model.revision))
    return _to_observation_response(model)


@router.patch(
    "/teaching/executions/{execution_id}/observations/{observation_id}",
    response_model=TeachingExecutionObservationResponse,
    operation_id="teaching_execution_observation_correct",
    responses=_OBSERVATION_CORRECT_RESPONSES,
)
def teaching_execution_observation_correct(
    execution_id: UUID,
    observation_id: UUID,
    body: TeachingExecutionObservationCorrectRequest,
    request: Request,
    response: Response,
    context: Annotated[TrustedSecurityContext, Depends(resolve_trusted_context)],
    service: Annotated[
        CorrectTeachingExecutionObservationService,
        Depends(correct_teaching_execution_observation_service),
    ],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> TeachingExecutionObservationResponse:
    key = parse_idempotency_key(idempotency_key)
    expected = ObservationRevision(parse_if_match(if_match))
    model = service.correct(
        context.tenant_id,
        context.principal_id,
        execution_id=_execution_id(execution_id),
        observation_id=_observation_id(observation_id),
        expected_revision=expected,
        command=CorrectTeachingExecutionObservationCommand(body=body.body),
        idempotency_key=key,
        event_context=_mutation_event_context(request, context),
        audit_provenance=api_mutation_audit_provenance(context.principal_id),
    )
    response.headers["ETag"] = encode_revision_etag(int(model.revision))
    return _to_observation_response(model)


@router.post(
    "/teaching/executions/{execution_id}/actions/complete",
    response_model=TeachingExecutionResponse,
    operation_id="teaching_execution_complete",
    responses=_EXECUTION_MUTATION_RESPONSES,
)
def teaching_execution_complete(
    execution_id: UUID,
    request: Request,
    response: Response,
    context: Annotated[TrustedSecurityContext, Depends(resolve_trusted_context)],
    service: Annotated[
        CompleteTeachingExecutionService, Depends(complete_teaching_execution_service)
    ],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> TeachingExecutionResponse:
    key = parse_idempotency_key(idempotency_key)
    expected = AggregateRevision(parse_if_match(if_match))
    model = service.complete(
        context.tenant_id,
        context.principal_id,
        execution_id=_execution_id(execution_id),
        expected_aggregate_revision=expected,
        idempotency_key=key,
        event_context=_mutation_event_context(request, context),
        audit_provenance=api_mutation_audit_provenance(context.principal_id),
    )
    response.headers["ETag"] = encode_revision_etag(int(model.aggregate_revision))
    return _to_execution_response(model)


@router.post(
    "/teaching/executions/{execution_id}/actions/cancel",
    response_model=TeachingExecutionResponse,
    operation_id="teaching_execution_cancel",
    responses=_EXECUTION_MUTATION_RESPONSES,
)
def teaching_execution_cancel(
    execution_id: UUID,
    request: Request,
    response: Response,
    context: Annotated[TrustedSecurityContext, Depends(resolve_trusted_context)],
    service: Annotated[
        CancelTeachingExecutionService, Depends(cancel_teaching_execution_service)
    ],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> TeachingExecutionResponse:
    key = parse_idempotency_key(idempotency_key)
    expected = AggregateRevision(parse_if_match(if_match))
    model = service.cancel(
        context.tenant_id,
        context.principal_id,
        execution_id=_execution_id(execution_id),
        expected_aggregate_revision=expected,
        idempotency_key=key,
        event_context=_mutation_event_context(request, context),
        audit_provenance=api_mutation_audit_provenance(context.principal_id),
    )
    response.headers["ETag"] = encode_revision_etag(int(model.aggregate_revision))
    return _to_execution_response(model)


@router.get(
    "/teacher-os/teach/context",
    response_model=TeacherOsTeachContextResponse,
    operation_id="teacher_os_teach_context_get",
    responses=_GET_RESPONSES,
    tags=["teacher-os"],
)
def teacher_os_teach_context_get(
    context: Annotated[TrustedSecurityContext, Depends(resolve_trusted_context)],
    service: Annotated[
        GetTeacherOsTeachContextService, Depends(teacher_os_teach_context_service)
    ],
    work_id: Annotated[UUID, Query()],
    class_ref: Annotated[str, Query(min_length=1)],
) -> TeacherOsTeachContextResponse:
    model = service.get(
        context.tenant_id,
        context.principal_id,
        GetTeacherOsTeachContextQuery(work_id=work_id, class_ref=class_ref),
    )
    return _to_teach_context_response(model)

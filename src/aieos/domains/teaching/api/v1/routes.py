"""Teaching HTTP v1. Calls application services only."""

from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response

from aieos.domains.teaching.api.v1.dependencies import (
    create_teaching_work_service,
    get_teaching_work_service,
    list_teaching_works_service,
    refine_teaching_work_service,
    resolve_trusted_context,
    teacher_os_today_mission_service,
)
from aieos.domains.teaching.api.v1.models import (
    ContinueWorkSummaryResponse,
    HeroActionResponse,
    PreparationProjectionResponse,
    ReviewProjectionResponse,
    TeacherOsMissionResponse,
    TeachingWorkCreateRequest,
    TeachingWorkListResponse,
    TeachingWorkRefineRequest,
    TeachingWorkResponse,
)
from aieos.domains.teaching.application.create import CreateTeachingWorkService
from aieos.domains.teaching.application.errors import InvalidTeachingWorkRequest
from aieos.domains.teaching.application.mission import GetTeacherOsTodayMissionService
from aieos.domains.teaching.application.mission_models import TeacherOsMission
from aieos.domains.teaching.application.models import (
    CreateTeachingWorkCommand,
    ListTeachingWorksQuery,
    RefineTeachingWorkCommand,
    TeachingWorkReadModel,
)
from aieos.domains.teaching.application.queries import (
    GetTeachingWorkService,
    ListTeachingWorksService,
)
from aieos.domains.teaching.application.refine import RefineTeachingWorkService
from aieos.domains.teaching.domain.errors import InvalidTeachingIdentityError
from aieos.domains.teaching.domain.identities import AggregateRevision, WorkId
from aieos.domains.teaching.domain.work import UNSET
from aieos.platform.api.etag import encode_revision_etag
from aieos.platform.api.idempotency_key import parse_idempotency_key
from aieos.platform.api.if_match import parse_if_match
from aieos.platform.api.problems import ProblemDetails
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


def _work_id(value: UUID) -> WorkId:
    try:
        return WorkId(value)
    except InvalidTeachingIdentityError as exc:
        raise InvalidTeachingWorkRequest("work_id must be a UUIDv7") from exc


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

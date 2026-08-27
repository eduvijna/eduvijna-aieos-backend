"""Teaching HTTP dependencies.

resolve_trusted_context is reused from the Content HTTP surface: request
identity, tenant authority, and authorization remain platform contracts and
are not re-implemented per domain.
"""

from __future__ import annotations

from fastapi import Request

from aieos.domains.content.api.v1.dependencies import resolve_trusted_context
from aieos.domains.teaching.application.create import CreateTeachingWorkService
from aieos.domains.teaching.application.mission import GetTeacherOsTodayMissionService
from aieos.domains.teaching.application.queries import (
    GetTeachingWorkService,
    ListTeachingWorksService,
)
from aieos.domains.teaching.application.refine import RefineTeachingWorkService

__all__ = [
    "create_teaching_work_service",
    "get_teaching_work_service",
    "list_teaching_works_service",
    "refine_teaching_work_service",
    "resolve_trusted_context",
    "teacher_os_today_mission_service",
]


def create_teaching_work_service(request: Request) -> CreateTeachingWorkService:
    return request.app.state.create_teaching_work_service


def refine_teaching_work_service(request: Request) -> RefineTeachingWorkService:
    return request.app.state.refine_teaching_work_service


def get_teaching_work_service(request: Request) -> GetTeachingWorkService:
    return request.app.state.get_teaching_work_service


def list_teaching_works_service(request: Request) -> ListTeachingWorksService:
    return request.app.state.list_teaching_works_service


def teacher_os_today_mission_service(
    request: Request,
) -> GetTeacherOsTodayMissionService:
    return request.app.state.teacher_os_today_mission_service

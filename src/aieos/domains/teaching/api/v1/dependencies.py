"""Teaching HTTP dependencies.

resolve_trusted_context is reused from the Content HTTP surface: request
identity, tenant authority, and authorization remain platform contracts and
are not re-implemented per domain.
"""

from __future__ import annotations

from fastapi import Request

from aieos.domains.content.api.v1.dependencies import resolve_trusted_context
from aieos.domains.teaching.application.artifacts import ListTeachingWorkArtifactsService
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
from aieos.domains.teaching.application.create import CreateTeachingWorkService
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
from aieos.domains.teaching.application.generate import GenerateTeachingWorkService
from aieos.domains.teaching.application.mission import GetTeacherOsTodayMissionService
from aieos.domains.teaching.application.prepare import PrepareTeachingWorkService
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
)

__all__ = [
    "cancel_teaching_assignment_service",
    "cancel_teaching_execution_service",
    "close_teaching_assignment_service",
    "complete_teaching_execution_service",
    "correct_teaching_execution_observation_service",
    "create_teaching_assignment_service",
    "create_teaching_execution_observation_service",
    "create_teaching_work_service",
    "generate_teaching_work_service",
    "get_teaching_assignment_service",
    "get_teaching_execution_service",
    "get_teaching_work_service",
    "list_assignable_school_classes_service",
    "list_teaching_assignment_service",
    "list_teaching_executions_service",
    "list_teaching_work_artifacts_service",
    "list_teaching_works_service",
    "prepare_teaching_work_service",
    "refine_teaching_work_service",
    "resolve_trusted_context",
    "start_teaching_execution_service",
    "teacher_os_teach_context_service",
    "teacher_os_today_mission_service",
    "update_teaching_assignment_due_service",
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


def generate_teaching_work_service(request: Request) -> GenerateTeachingWorkService:
    service = request.app.state.generate_teaching_work_service
    if service is None:
        from aieos.domains.teaching.application.errors import GenerationServiceUnavailable

        raise GenerationServiceUnavailable(
            "Teaching Work generation is not composed in this runtime"
        )
    return service


def prepare_teaching_work_service(request: Request) -> PrepareTeachingWorkService:
    service = request.app.state.prepare_teaching_work_service
    if service is None:
        from aieos.domains.teaching.application.errors import GenerationServiceUnavailable

        raise GenerationServiceUnavailable(
            "Teaching Work preparation is not composed in this runtime"
        )
    return service


def list_teaching_work_artifacts_service(
    request: Request,
) -> ListTeachingWorkArtifactsService:
    service = request.app.state.list_teaching_work_artifacts_service
    if service is None:
        from aieos.domains.teaching.application.errors import GenerationServiceUnavailable

        raise GenerationServiceUnavailable(
            "Teaching Work artifacts are not composed in this runtime"
        )
    return service


def list_assignable_school_classes_service(
    request: Request,
) -> ListAssignableSchoolClassesService:
    service = request.app.state.list_assignable_school_classes_service
    if service is None:
        from aieos.domains.teaching.application.errors import SchoolContextUnavailable

        raise SchoolContextUnavailable(
            "School Context is not composed in this runtime"
        )
    return service


def _require_assignment_services(request: Request) -> None:
    if request.app.state.school_context_class_authority is None:
        from aieos.domains.teaching.application.errors import SchoolContextUnavailable

        raise SchoolContextUnavailable(
            "School Context is not composed in this runtime"
        )


def create_teaching_assignment_service(
    request: Request,
) -> CreateTeachingAssignmentService:
    _require_assignment_services(request)
    service = request.app.state.create_teaching_assignment_service
    if service is None:
        from aieos.domains.teaching.application.errors import SchoolContextUnavailable

        raise SchoolContextUnavailable(
            "TeachingAssignment commands are not composed in this runtime"
        )
    return service


def get_teaching_assignment_service(
    request: Request,
) -> GetTeachingAssignmentService:
    return request.app.state.get_teaching_assignment_service


def list_teaching_assignment_service(
    request: Request,
) -> ListTeachingAssignmentsService:
    return request.app.state.list_teaching_assignment_service


def update_teaching_assignment_due_service(
    request: Request,
) -> UpdateTeachingAssignmentDueService:
    service = request.app.state.update_teaching_assignment_due_service
    if service is None:
        from aieos.domains.teaching.application.errors import SchoolContextUnavailable

        raise SchoolContextUnavailable(
            "TeachingAssignment commands are not composed in this runtime"
        )
    return service


def close_teaching_assignment_service(
    request: Request,
) -> CloseTeachingAssignmentService:
    service = request.app.state.close_teaching_assignment_service
    if service is None:
        from aieos.domains.teaching.application.errors import SchoolContextUnavailable

        raise SchoolContextUnavailable(
            "TeachingAssignment commands are not composed in this runtime"
        )
    return service


def cancel_teaching_assignment_service(
    request: Request,
) -> CancelTeachingAssignmentService:
    service = request.app.state.cancel_teaching_assignment_service
    if service is None:
        from aieos.domains.teaching.application.errors import SchoolContextUnavailable

        raise SchoolContextUnavailable(
            "TeachingAssignment commands are not composed in this runtime"
        )
    return service


def _require_execution_mutation_services(request: Request) -> None:
    if request.app.state.school_context_class_authority is None:
        from aieos.domains.teaching.application.errors import SchoolContextUnavailable

        raise SchoolContextUnavailable(
            "School Context is not composed in this runtime"
        )


def start_teaching_execution_service(
    request: Request,
) -> StartTeachingExecutionService:
    _require_execution_mutation_services(request)
    service = request.app.state.start_teaching_execution_service
    if service is None:
        from aieos.domains.teaching.application.errors import SchoolContextUnavailable

        raise SchoolContextUnavailable(
            "TeachingExecution commands are not composed in this runtime"
        )
    return service


def complete_teaching_execution_service(
    request: Request,
) -> CompleteTeachingExecutionService:
    service = request.app.state.complete_teaching_execution_service
    if service is None:
        from aieos.domains.teaching.application.errors import SchoolContextUnavailable

        raise SchoolContextUnavailable(
            "TeachingExecution commands are not composed in this runtime"
        )
    return service


def cancel_teaching_execution_service(
    request: Request,
) -> CancelTeachingExecutionService:
    service = request.app.state.cancel_teaching_execution_service
    if service is None:
        from aieos.domains.teaching.application.errors import SchoolContextUnavailable

        raise SchoolContextUnavailable(
            "TeachingExecution commands are not composed in this runtime"
        )
    return service


def create_teaching_execution_observation_service(
    request: Request,
) -> CreateTeachingExecutionObservationService:
    service = request.app.state.create_teaching_execution_observation_service
    if service is None:
        from aieos.domains.teaching.application.errors import SchoolContextUnavailable

        raise SchoolContextUnavailable(
            "TeachingExecution commands are not composed in this runtime"
        )
    return service


def correct_teaching_execution_observation_service(
    request: Request,
) -> CorrectTeachingExecutionObservationService:
    service = request.app.state.correct_teaching_execution_observation_service
    if service is None:
        from aieos.domains.teaching.application.errors import SchoolContextUnavailable

        raise SchoolContextUnavailable(
            "TeachingExecution commands are not composed in this runtime"
        )
    return service


def get_teaching_execution_service(
    request: Request,
) -> GetTeachingExecutionService:
    return request.app.state.get_teaching_execution_service


def list_teaching_executions_service(
    request: Request,
) -> ListTeachingExecutionsService:
    return request.app.state.list_teaching_executions_service


def teacher_os_teach_context_service(
    request: Request,
) -> GetTeacherOsTeachContextService:
    _require_execution_mutation_services(request)
    service = request.app.state.teacher_os_teach_context_service
    if service is None:
        from aieos.domains.teaching.application.errors import (
            GenerationServiceUnavailable,
            SchoolContextUnavailable,
        )

        if request.app.state.list_teaching_work_artifacts_service is None:
            raise GenerationServiceUnavailable(
                "Teaching Work artifacts are not composed in this runtime"
            )
        raise SchoolContextUnavailable(
            "Teacher OS Teach context is not composed in this runtime"
        )
    return service

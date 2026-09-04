"""GET / LIST ClassroomAssessment queries.

Historical reads: no current ClassRef gate. Exact ADR-AIEOS-031 read/list
capability ALLOW is still required on every request.
"""

from __future__ import annotations

from uuid import UUID

from aieos.domains.assessment.application.errors import (
    ClassroomAssessmentNotFound,
    InvalidClassroomAssessmentRequest,
)
from aieos.domains.assessment.application.models import (
    ClassroomAssessmentReadModel,
    ListClassroomAssessmentsQuery,
    classroom_assessment_read_model,
)
from aieos.domains.assessment.application.ports import (
    ASSESSMENT_CLASSROOM_LIST,
    ASSESSMENT_CLASSROOM_READ,
    AssessmentUnitOfWorkFactory,
    ClassroomAssessmentAuthorization,
)
from aieos.domains.assessment.domain.identities import AssessmentId


class GetClassroomAssessmentService:
    def __init__(
        self,
        uow_factory: AssessmentUnitOfWorkFactory,
        authorization: ClassroomAssessmentAuthorization,
    ) -> None:
        self._uow_factory = uow_factory
        self._authorization = authorization

    def get(
        self,
        execution_tenant_id: UUID,
        principal_id: UUID,
        assessment_id: AssessmentId,
    ) -> ClassroomAssessmentReadModel:
        self._authorization.authorize(
            tenant_id=execution_tenant_id,
            principal_id=principal_id,
            capability=ASSESSMENT_CLASSROOM_READ,
        )
        with self._uow_factory(execution_tenant_id) as uow:
            found = uow.classroom_assessments.get(assessment_id)
            if found is None or found.teacher_principal_id != principal_id:
                raise ClassroomAssessmentNotFound(
                    "ClassroomAssessment is not visible in the execution tenant"
                )
            return classroom_assessment_read_model(found)


class ListClassroomAssessmentsService:
    def __init__(
        self,
        uow_factory: AssessmentUnitOfWorkFactory,
        authorization: ClassroomAssessmentAuthorization,
    ) -> None:
        self._uow_factory = uow_factory
        self._authorization = authorization

    def list(
        self,
        execution_tenant_id: UUID,
        principal_id: UUID,
        query: ListClassroomAssessmentsQuery,
    ) -> list[ClassroomAssessmentReadModel]:
        self._authorization.authorize(
            tenant_id=execution_tenant_id,
            principal_id=principal_id,
            capability=ASSESSMENT_CLASSROOM_LIST,
        )
        if query.limit < 1 or query.limit > 100:
            raise InvalidClassroomAssessmentRequest(
                "list limit must be between 1 and 100"
            )
        with self._uow_factory(execution_tenant_id) as uow:
            rows = uow.classroom_assessments.list_for_teacher(
                principal_id,
                class_ref=query.class_ref,
                work_id=query.work_id,
                execution_id=query.execution_id,
                assignment_id=query.assignment_id,
                lifecycle_state=query.lifecycle_state,
                limit=query.limit,
            )
            return [classroom_assessment_read_model(row) for row in rows]

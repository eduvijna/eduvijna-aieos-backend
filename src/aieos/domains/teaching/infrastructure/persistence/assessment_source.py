"""Assessment source adapter sharing the Teaching transaction connection."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.engine import Connection

from aieos.domains.assessment.domain.errors import AssessmentDomainError
from aieos.domains.assessment.domain.identities import AssessmentId
from aieos.domains.assessment.infrastructure.persistence.repositories import (
    SqlAlchemyClassroomAssessmentRepository,
)
from aieos.domains.teaching.application.ports import (
    RemediationAssessmentSourceSnapshot,
)


class SqlAlchemyRemediationAssessmentSource:
    """Locks Assessment through its repository, then maps to a Teaching DTO."""

    def __init__(self, connection: Connection, execution_tenant_id: UUID) -> None:
        self._repository = SqlAlchemyClassroomAssessmentRepository(
            connection, execution_tenant_id
        )

    def load_for_update(
        self, assessment_id: UUID
    ) -> RemediationAssessmentSourceSnapshot | None:
        try:
            typed_id = AssessmentId(assessment_id)
        except AssessmentDomainError:
            return None
        assessment = self._repository.get_for_update(typed_id)
        if assessment is None:
            return None
        return RemediationAssessmentSourceSnapshot(
            assessment_id=assessment.assessment_id.value,
            tenant_id=assessment.tenant_id,
            teacher_principal_id=assessment.teacher_principal_id,
            class_ref=assessment.class_ref,
            content_id=assessment.content_id,
            content_version_id=assessment.content_version_id,
            class_result_level=assessment.class_result_level.value,
            lifecycle_state=assessment.lifecycle_state.value,
            work_id=assessment.work_id,
            execution_id=assessment.execution_id,
            assignment_id=assessment.assignment_id,
            aggregate_revision=int(assessment.aggregate_revision),
        )

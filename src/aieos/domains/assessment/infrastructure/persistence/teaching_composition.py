"""Read-only Teaching composition adapter for Assessment Cases A/B and work_id.

Does not update Teaching or Content aggregates and does not create FKs.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.engine import Connection

from aieos.domains.assessment.application.composition import (
    ASSESSMENT_ELIGIBLE_CONTENT_TYPES,
)
from aieos.domains.assessment.application.errors import (
    TeachingAssignmentCompositionMismatch,
    TeachingAssignmentForbidden,
    TeachingAssignmentNotFound,
    TeachingExecutionBindingMismatch,
    TeachingExecutionForbidden,
    TeachingExecutionNotCompleted,
    TeachingExecutionNotFound,
    TeachingWorkForbidden,
    TeachingWorkNotFound,
)
from aieos.domains.teaching.domain.audience_type import AudienceType
from aieos.domains.teaching.domain.execution_lifecycle import ExecutionLifecycleState
from aieos.domains.teaching.domain.identities import (
    AssignmentId,
    ExecutionId,
    WorkId,
)
from aieos.domains.teaching.infrastructure.persistence.repositories import (
    SqlAlchemyTeachingAssignmentRepository,
    SqlAlchemyTeachingExecutionRepository,
    SqlAlchemyTeachingWorkRepository,
)


class SqlAlchemyAssessmentTeachingCompositionAdapter:
    def __init__(self, connection: Connection, execution_tenant_id: UUID) -> None:
        self._works = SqlAlchemyTeachingWorkRepository(
            connection, execution_tenant_id
        )
        self._assignments = SqlAlchemyTeachingAssignmentRepository(
            connection, execution_tenant_id
        )
        self._executions = SqlAlchemyTeachingExecutionRepository(
            connection, execution_tenant_id
        )

    def load_completed_execution(
        self,
        *,
        execution_id: UUID,
        teacher_principal_id: UUID,
        class_ref: str,
        content_id: UUID,
        content_version_id: UUID,
        work_id: UUID | None,
    ) -> UUID:
        execution = self._executions.get(ExecutionId(execution_id))
        if execution is None:
            raise TeachingExecutionNotFound(
                "TeachingExecution is not visible in the execution tenant"
            )
        if execution.teacher_principal_id != teacher_principal_id:
            raise TeachingExecutionForbidden(
                "TeachingExecution is owned by a different teacher"
            )
        if execution.class_ref != class_ref.strip():
            raise TeachingExecutionBindingMismatch(
                "TeachingExecution class_ref does not match the Assessment request"
            )
        if execution.lifecycle_state is not ExecutionLifecycleState.COMPLETED:
            raise TeachingExecutionNotCompleted(
                "Case A requires a COMPLETED TeachingExecution"
            )
        if work_id is not None and execution.work_id.value != work_id:
            raise TeachingExecutionBindingMismatch(
                "supplied work_id does not equal TeachingExecution.work_id"
            )
        bindings = self._executions.list_bindings(execution.execution_id)
        matched = False
        for binding in bindings:
            if (
                binding.content_id == content_id
                and binding.content_version_id == content_version_id
            ):
                if binding.artifact_kind not in ASSESSMENT_ELIGIBLE_CONTENT_TYPES:
                    raise TeachingExecutionBindingMismatch(
                        "execution binding artifact_kind is not Assessment-eligible"
                    )
                matched = True
                break
        if not matched:
            raise TeachingExecutionBindingMismatch(
                "exact ContentVersion is not bound on the TeachingExecution"
            )
        return execution.work_id.value

    def load_class_assignment(
        self,
        *,
        assignment_id: UUID,
        teacher_principal_id: UUID,
        class_ref: str,
        content_id: UUID,
        content_version_id: UUID,
        work_id: UUID | None,
    ) -> UUID | None:
        assignment = self._assignments.get(AssignmentId(assignment_id))
        if assignment is None:
            raise TeachingAssignmentNotFound(
                "TeachingAssignment is not visible in the execution tenant"
            )
        if assignment.teacher_principal_id != teacher_principal_id:
            raise TeachingAssignmentForbidden(
                "TeachingAssignment is owned by a different teacher"
            )
        if assignment.class_ref != class_ref.strip():
            raise TeachingAssignmentCompositionMismatch(
                "TeachingAssignment class_ref does not match the Assessment request"
            )
        if assignment.audience_type is not AudienceType.CLASS:
            raise TeachingAssignmentCompositionMismatch(
                "Case B requires audience_type CLASS"
            )
        if (
            assignment.content_id != content_id
            or assignment.content_version_id != content_version_id
        ):
            raise TeachingAssignmentCompositionMismatch(
                "exact ContentVersion does not match TeachingAssignment binding"
            )
        source_work = (
            assignment.source_work_id.value
            if assignment.source_work_id is not None
            else None
        )
        if work_id is not None and source_work is not None and work_id != source_work:
            raise TeachingAssignmentCompositionMismatch(
                "supplied work_id does not equal TeachingAssignment.source_work_id"
            )
        return source_work

    def require_owned_work(
        self,
        *,
        work_id: UUID,
        teacher_principal_id: UUID,
    ) -> None:
        work = self._works.get(WorkId(work_id))
        if work is None:
            raise TeachingWorkNotFound(
                "TeachingWork is not visible in the execution tenant"
            )
        if work.teacher_principal_id != teacher_principal_id:
            raise TeachingWorkForbidden(
                "TeachingWork is owned by a different teacher"
            )

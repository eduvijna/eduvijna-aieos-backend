"""Teacher OS Teach composition read (projection only — no SoR writes)."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from aieos.domains.teaching.application.artifacts import (
    ListTeachingWorkArtifactsService,
    WorkArtifactsResult,
)
from aieos.domains.teaching.application.errors import (
    TeachingWorkForbidden,
    TeachingWorkNotFound,
)
from aieos.domains.teaching.application.models import (
    GetTeacherOsTeachContextQuery,
    TeachingAssignmentReadModel,
    TeachingExecutionReadModel,
    TeachingWorkReadModel,
    teaching_assignment_read_model,
    teaching_execution_read_model,
    teaching_work_read_model,
)
from aieos.domains.teaching.application.ports import TeachingUnitOfWorkFactory
from aieos.domains.teaching.application.school_context import (
    SchoolContextClassAuthority,
)
from aieos.domains.teaching.domain.identities import WorkId

# Composition relevance scan — assignments lack work_id/class_ref list filters.
_ASSIGNMENT_SCAN_LIMIT = 100


@dataclass(frozen=True, slots=True)
class TeacherOsTeachContextReadModel:
    work: TeachingWorkReadModel
    class_ref: str
    class_display_label: str
    artifacts: WorkArtifactsResult
    assignments: tuple[TeachingAssignmentReadModel, ...]
    executions: tuple[TeachingExecutionReadModel, ...]


class GetTeacherOsTeachContextService:
    """Compose actionable Teach context for one Work + currently authorized ClassRef.

    Projection only. Fail closed when School Context is unavailable.
    """

    def __init__(
        self,
        uow_factory: TeachingUnitOfWorkFactory,
        class_authority: SchoolContextClassAuthority,
        artifacts: ListTeachingWorkArtifactsService,
    ) -> None:
        self._uow_factory = uow_factory
        self._class_authority = class_authority
        self._artifacts = artifacts

    def get(
        self,
        execution_tenant_id: UUID,
        principal_id: UUID,
        query: GetTeacherOsTeachContextQuery,
    ) -> TeacherOsTeachContextReadModel:
        work_id = WorkId(query.work_id)
        class_target = self._class_authority.require_assignable_class_ref(
            execution_tenant_id, principal_id, query.class_ref
        )
        with self._uow_factory(execution_tenant_id) as uow:
            work = uow.works.get(work_id)
            if work is None:
                raise TeachingWorkNotFound(
                    "TeachingWork is not visible in the execution tenant"
                )
            if work.teacher_principal_id != principal_id:
                raise TeachingWorkForbidden(
                    "TeachingWork is owned by a different teacher"
                )
            assignment_rows = uow.assignments.list_for_teacher(
                teacher_principal_id=principal_id,
                limit=_ASSIGNMENT_SCAN_LIMIT,
            )
            execution_rows = uow.executions.list_for_teacher(
                teacher_principal_id=principal_id,
                limit=_ASSIGNMENT_SCAN_LIMIT,
                work_id=work_id,
                class_ref=class_target.class_ref,
            )

        artifacts = self._artifacts.list(
            execution_tenant_id, principal_id, work_id
        )
        relevant_assignments = tuple(
            teaching_assignment_read_model(row)
            for row in assignment_rows
            if row.source_work_id is not None
            and row.source_work_id == work_id
            and row.class_ref == class_target.class_ref
        )
        return TeacherOsTeachContextReadModel(
            work=teaching_work_read_model(work),
            class_ref=class_target.class_ref,
            class_display_label=class_target.display_label,
            artifacts=artifacts,
            assignments=relevant_assignments,
            executions=tuple(
                teaching_execution_read_model(row) for row in execution_rows
            ),
        )

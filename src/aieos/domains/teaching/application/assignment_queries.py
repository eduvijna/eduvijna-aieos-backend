"""TeachingAssignment read services."""

from __future__ import annotations

from uuid import UUID

from aieos.domains.teaching.application.errors import (
    InvalidTeachingAssignmentRequest,
    TeachingAssignmentForbidden,
    TeachingAssignmentNotFound,
)
from aieos.domains.teaching.application.models import (
    ListTeachingAssignmentsQuery,
    ListTeachingAssignmentsResult,
    TeachingAssignmentReadModel,
    teaching_assignment_read_model,
)
from aieos.domains.teaching.application.ports import TeachingUnitOfWorkFactory
from aieos.domains.teaching.domain.assignment_lifecycle import (
    AssignmentLifecycleState,
    parse_lifecycle_state,
)
from aieos.domains.teaching.domain.errors import InvalidTeachingAssignmentError
from aieos.domains.teaching.domain.identities import AssignmentId


class GetTeachingAssignmentService:
    def __init__(self, uow_factory: TeachingUnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def get(
        self,
        execution_tenant_id: UUID,
        principal_id: UUID,
        assignment_id: AssignmentId,
    ) -> TeachingAssignmentReadModel:
        with self._uow_factory(execution_tenant_id) as uow:
            found = uow.assignments.get(assignment_id)
        if found is None:
            raise TeachingAssignmentNotFound(
                "TeachingAssignment is not visible in the execution tenant"
            )
        if found.teacher_principal_id != principal_id:
            raise TeachingAssignmentForbidden(
                "TeachingAssignment is owned by a different teacher"
            )
        return teaching_assignment_read_model(found)


class ListTeachingAssignmentsService:
    def __init__(self, uow_factory: TeachingUnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def list(
        self,
        execution_tenant_id: UUID,
        principal_id: UUID,
        query: ListTeachingAssignmentsQuery,
    ) -> ListTeachingAssignmentsResult:
        lifecycle: str | None = None
        if query.lifecycle_state is not None:
            try:
                lifecycle = parse_lifecycle_state(query.lifecycle_state).value
            except InvalidTeachingAssignmentError as exc:
                raise InvalidTeachingAssignmentRequest(
                    "lifecycle_state filter is invalid"
                ) from exc
        page_size = query.limit + 1
        with self._uow_factory(execution_tenant_id) as uow:
            rows = uow.assignments.list_for_teacher(
                teacher_principal_id=principal_id,
                limit=page_size,
                lifecycle_state=lifecycle,
            )
        has_more = len(rows) > query.limit
        items = tuple(
            teaching_assignment_read_model(item)
            for item in rows[: query.limit]
        )
        return ListTeachingAssignmentsResult(items=items, has_more=has_more)

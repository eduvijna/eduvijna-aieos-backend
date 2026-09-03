"""TeachingExecution read services."""

from __future__ import annotations

from uuid import UUID

from aieos.domains.teaching.application.errors import (
    InvalidTeachingExecutionRequest,
    TeachingExecutionForbidden,
    TeachingExecutionNotFound,
)
from aieos.domains.teaching.application.models import (
    ListTeachingExecutionsQuery,
    ListTeachingExecutionsResult,
    TeachingExecutionReadModel,
    teaching_execution_read_model,
)
from aieos.domains.teaching.application.ports import TeachingUnitOfWorkFactory
from aieos.domains.teaching.domain.errors import InvalidTeachingExecutionError
from aieos.domains.teaching.domain.execution_lifecycle import (
    parse_execution_lifecycle_state,
)
from aieos.domains.teaching.domain.identities import ExecutionId, WorkId

MAX_LIST_LIMIT = 100


class GetTeachingExecutionService:
    def __init__(self, uow_factory: TeachingUnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def get(
        self,
        execution_tenant_id: UUID,
        principal_id: UUID,
        execution_id: ExecutionId,
    ) -> TeachingExecutionReadModel:
        with self._uow_factory(execution_tenant_id) as uow:
            found = uow.executions.get(execution_id)
            if found is None:
                raise TeachingExecutionNotFound(
                    "TeachingExecution is not visible in the execution tenant"
                )
            if found.teacher_principal_id != principal_id:
                raise TeachingExecutionForbidden(
                    "TeachingExecution is owned by a different teacher"
                )
            observations = tuple(uow.executions.list_observations(execution_id))
        return teaching_execution_read_model(found, observations=observations)


class ListTeachingExecutionsService:
    def __init__(self, uow_factory: TeachingUnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def list(
        self,
        execution_tenant_id: UUID,
        principal_id: UUID,
        query: ListTeachingExecutionsQuery,
    ) -> ListTeachingExecutionsResult:
        if query.limit < 1 or query.limit > MAX_LIST_LIMIT:
            raise InvalidTeachingExecutionRequest(
                "list limit must be an integer from 1 to 100"
            )
        lifecycle: str | None = None
        if query.lifecycle_state is not None:
            try:
                lifecycle = parse_execution_lifecycle_state(
                    query.lifecycle_state
                ).value
            except InvalidTeachingExecutionError as exc:
                raise InvalidTeachingExecutionRequest(
                    "lifecycle_state filter is invalid"
                ) from exc
        work_id: WorkId | None = None
        if query.work_id is not None:
            work_id = WorkId(query.work_id)
        class_ref = None if query.class_ref is None else query.class_ref.strip()
        page_size = query.limit + 1
        with self._uow_factory(execution_tenant_id) as uow:
            rows = uow.executions.list_for_teacher(
                teacher_principal_id=principal_id,
                limit=page_size,
                work_id=work_id,
                class_ref=class_ref,
                lifecycle_state=lifecycle,
            )
        has_more = len(rows) > query.limit
        items = tuple(
            teaching_execution_read_model(item) for item in rows[: query.limit]
        )
        return ListTeachingExecutionsResult(items=items, has_more=has_more)

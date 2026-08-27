"""Read durable TeachingWork rows owned by the requesting teacher."""

from __future__ import annotations

from uuid import UUID

from aieos.domains.teaching.application.errors import (
    InvalidTeachingWorkRequest,
    TeachingWorkForbidden,
    TeachingWorkNotFound,
)
from aieos.domains.teaching.application.models import (
    ListTeachingWorksQuery,
    ListTeachingWorksResult,
    TeachingWorkReadModel,
    teaching_work_read_model,
)
from aieos.domains.teaching.application.ports import TeachingUnitOfWorkFactory
from aieos.domains.teaching.domain.identities import WorkId

MAX_LIST_LIMIT = 100


class GetTeachingWorkService:
    def __init__(self, uow_factory: TeachingUnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def get(
        self,
        execution_tenant_id: UUID,
        principal_id: UUID,
        work_id: WorkId,
    ) -> TeachingWorkReadModel:
        with self._uow_factory(execution_tenant_id) as uow:
            found = uow.works.get(work_id)
        if found is None:
            raise TeachingWorkNotFound(
                "TeachingWork is not visible in the execution tenant"
            )
        if found.teacher_principal_id != principal_id:
            raise TeachingWorkForbidden("TeachingWork is owned by a different teacher")
        return teaching_work_read_model(found)


class ListTeachingWorksService:
    """Teacher-scoped list. Archived Work is excluded from the active list."""

    def __init__(self, uow_factory: TeachingUnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def list(
        self,
        execution_tenant_id: UUID,
        principal_id: UUID,
        query: ListTeachingWorksQuery,
    ) -> ListTeachingWorksResult:
        if query.limit < 1 or query.limit > MAX_LIST_LIMIT:
            raise InvalidTeachingWorkRequest(
                "list limit must be an integer from 1 to 100"
            )
        with self._uow_factory(execution_tenant_id) as uow:
            rows = uow.works.list_for_teacher(
                teacher_principal_id=principal_id,
                limit=query.limit + 1,
                include_archived=query.include_archived,
            )
        has_more = len(rows) > query.limit
        items = tuple(teaching_work_read_model(row) for row in rows[: query.limit])
        return ListTeachingWorksResult(items=items, has_more=has_more)

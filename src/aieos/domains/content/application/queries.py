"""Read Content aggregates visible in the execution tenant."""

from __future__ import annotations

from uuid import UUID

from aieos.domains.content.application.errors import ContentNotFound, InvalidContentRequest
from aieos.domains.content.application.models import (
    ContentReadModel,
    ListContentsQuery,
    ListContentsResult,
    content_read_model,
)
from aieos.domains.content.application.ports import ContentUnitOfWorkFactory
from aieos.domains.content.domain.identities import ContentId


class GetContentService:
    def __init__(self, uow_factory: ContentUnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def get(
        self,
        execution_tenant_id: UUID,
        content_id: ContentId,
    ) -> ContentReadModel:
        with self._uow_factory(execution_tenant_id) as uow:
            found = uow.contents.get(content_id)
        if found is None:
            raise ContentNotFound("Content is not visible in the execution tenant")
        return content_read_model(found)


class ListContentsService:
    def __init__(self, uow_factory: ContentUnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def list(
        self,
        execution_tenant_id: UUID,
        query: ListContentsQuery,
    ) -> ListContentsResult:
        if query.limit < 1:
            raise InvalidContentRequest("list limit must be a positive integer")
        with self._uow_factory(execution_tenant_id) as uow:
            rows = uow.contents.list_page(
                limit=query.limit + 1,
                after_created_at=query.after_created_at,
                after_content_id=query.after_content_id,
            )
        has_more = len(rows) > query.limit
        items = tuple(content_read_model(row) for row in rows[: query.limit])
        return ListContentsResult(items=items, has_more=has_more)

"""Teacher OS Review Queue read services. No mutations."""

from __future__ import annotations

from uuid import UUID

from aieos.domains.content.application.errors import (
    InvalidContentRequest,
    ReviewQueueItemNotFound,
)
from aieos.domains.content.application.ports import ContentUnitOfWorkFactory
from aieos.domains.content.application.review_queue_models import (
    ListTeacherReviewQueueQuery,
    TeacherReviewQueueDetail,
    TeacherReviewQueueItem,
    TeacherReviewQueuePage,
)
from aieos.domains.content.domain.identities import ContentId, ContentVersionId


class ListTeacherReviewQueueService:
    def __init__(self, uow_factory: ContentUnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def list(
        self,
        execution_tenant_id: UUID,
        query: ListTeacherReviewQueueQuery,
    ) -> TeacherReviewQueuePage:
        if query.limit < 1:
            raise InvalidContentRequest("list limit must be a positive integer")
        with self._uow_factory(execution_tenant_id) as uow:
            rows = uow.review_queue.list_page(
                limit=query.limit + 1,
                after_submitted_at=query.after_submitted_at,
                after_content_id=query.after_content_id,
            )
        has_more = len(rows) > query.limit
        items = tuple(rows[: query.limit])
        return TeacherReviewQueuePage(items=items, has_more=has_more)


class GetTeacherReviewQueueItemService:
    def __init__(self, uow_factory: ContentUnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def get(
        self,
        execution_tenant_id: UUID,
        content_id: ContentId,
        version_id: ContentVersionId,
    ) -> TeacherReviewQueueDetail:
        with self._uow_factory(execution_tenant_id) as uow:
            found = uow.review_queue.get_item(content_id, version_id)
        if found is None:
            raise ReviewQueueItemNotFound(
                "Review Queue item is not visible or no longer eligible"
            )
        return found

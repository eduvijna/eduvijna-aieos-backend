"""Pending Review Queue count for the Today's Mission projection.

Teaching does not own the Review Queue and must not read Content tables
directly. The adapter below composes the existing Content read service through
its published application contract.
"""

from __future__ import annotations

from typing import Final
from uuid import UUID

from aieos.domains.content.application.review_queue import ListTeacherReviewQueueService
from aieos.domains.content.application.review_queue_models import (
    ListTeacherReviewQueueQuery,
)

PAGE_LIMIT: Final = 100
MAX_PAGES: Final = 50


class ReviewQueuePendingCountAdapter:
    """Counts pending Review Queue items by walking the published projection.

    The Review Queue exposes pages, not a count. Paging is bounded by
    MAX_PAGES so a large queue can never turn a Mission read into an unbounded
    scan; the reported count saturates at PAGE_LIMIT * MAX_PAGES.
    """

    def __init__(self, review_queue_service: ListTeacherReviewQueueService) -> None:
        self._review_queue_service = review_queue_service

    def pending_count(self, execution_tenant_id: UUID) -> int:
        total = 0
        after_submitted_at = None
        after_content_id = None
        for _ in range(MAX_PAGES):
            page = self._review_queue_service.list(
                execution_tenant_id,
                ListTeacherReviewQueueQuery(
                    limit=PAGE_LIMIT,
                    after_submitted_at=after_submitted_at,
                    after_content_id=after_content_id,
                ),
            )
            total += len(page.items)
            if not page.has_more or not page.items:
                return total
            last = page.items[-1]
            after_submitted_at = last.submitted_at
            after_content_id = last.content_id
        return total

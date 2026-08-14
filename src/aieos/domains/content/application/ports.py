"""Persistence ports. Infrastructure types are not part of this contract."""

from __future__ import annotations

from datetime import datetime
from typing import Mapping, Protocol
from uuid import UUID

from aieos.domains.content.application.models import LockedContentHead
from aieos.domains.content.domain.content import Content
from aieos.domains.content.domain.identities import (
    AggregateRevision,
    ContentId,
    ContentVersionId,
    ReviewDecisionId,
)
from aieos.domains.content.domain.review import ReviewDecision
from aieos.domains.content.domain.version import ContentVersion
from aieos.platform.idempotency.ports import IdempotencyRepository

CONTENT_REVIEW_SUBMIT = "content.review.submit"
CONTENT_REVIEW_DECIDE = "content.review.decide"


class ContentVersionRepository(Protocol):
    """INSERT/READ persistence for immutable ContentVersion rows."""

    def insert(
        self,
        version: ContentVersion,
        provenance: Mapping[str, object] | None,
    ) -> None: ...

    def get(self, version_id: ContentVersionId) -> ContentVersion | None: ...


class ContentTypeCatalog(Protocol):
    def contains(self, content_type: str) -> bool: ...


class ReviewRepository(Protocol):
    """INSERT/READ persistence for immutable ReviewDecision rows."""

    def insert(self, decision: ReviewDecision) -> None: ...

    def get(self, review_decision_id: ReviewDecisionId) -> ReviewDecision | None: ...

    def get_for_version(
        self, content_id: ContentId, version_id: ContentVersionId
    ) -> ReviewDecision | None: ...


class ReviewAuthorizationPort(Protocol):
    """Current capability check. Does not own users, roles, JWT, or policy rules."""

    def authorize(
        self,
        *,
        tenant_id: UUID,
        principal_id: UUID,
        content_id: ContentId,
        version_id: ContentVersionId,
        capability: str,
    ) -> None: ...


class ReviewCommentPolicy(Protocol):
    """Approve or reject a proposed review comment before first persistence."""

    def evaluate(self, comment: str | None) -> None: ...


class ContentRepository(Protocol):
    def insert(self, content: Content) -> None: ...

    def get(self, content_id: ContentId) -> Content | None: ...

    def list_page(
        self,
        *,
        limit: int,
        after_created_at: datetime | None,
        after_content_id: ContentId | None,
    ) -> list[Content]: ...

    def get_head_for_update(self, content_id: ContentId) -> LockedContentHead | None: ...

    def advance_current_version(
        self,
        *,
        content_id: ContentId,
        tenant_id: UUID,
        expected_revision: AggregateRevision,
        expected_current_version_id: ContentVersionId | None,
        new_version_id: ContentVersionId,
        updated_at: datetime,
    ) -> AggregateRevision | None: ...

    def transition_stewardship(
        self,
        *,
        content_id: ContentId,
        tenant_id: UUID,
        expected_revision: AggregateRevision,
        expected_current_version_id: ContentVersionId,
        expected_state: str,
        target_state: str,
        updated_at: datetime,
    ) -> AggregateRevision | None: ...


class ContentUnitOfWork(Protocol):
    contents: ContentRepository
    versions: ContentVersionRepository
    reviews: ReviewRepository
    idempotency: IdempotencyRepository

    def __enter__(self) -> ContentUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object,
    ) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


class ContentUnitOfWorkFactory(Protocol):
    def __call__(self, execution_tenant_id: UUID) -> ContentUnitOfWork: ...

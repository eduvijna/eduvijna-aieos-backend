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
)
from aieos.domains.content.domain.version import ContentVersion
from aieos.platform.idempotency.ports import IdempotencyRepository


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


class ContentUnitOfWork(Protocol):
    contents: ContentRepository
    versions: ContentVersionRepository
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

"""Persistence ports. Infrastructure types are not part of this contract."""

from __future__ import annotations

from datetime import datetime
from typing import Mapping, Protocol
from uuid import UUID

from aieos.domains.content.application.models import LockedContentHead
from aieos.domains.content.domain.identities import (
    AggregateRevision,
    ContentId,
    ContentVersionId,
)
from aieos.domains.content.domain.version import ContentVersion


class ContentVersionRepository(Protocol):
    """INSERT/READ persistence for immutable ContentVersion rows."""

    def insert(
        self,
        version: ContentVersion,
        provenance: Mapping[str, object] | None,
    ) -> None: ...

    def get(self, version_id: ContentVersionId) -> ContentVersion | None: ...


class ContentRepository(Protocol):
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

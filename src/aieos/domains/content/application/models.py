"""Application command/result contracts for Generic Content."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping
from uuid import UUID

from aieos.domains.content.domain.content import Content
from aieos.domains.content.domain.identities import (
    AggregateRevision,
    ContentId,
    ContentVersionId,
    VersionNumber,
)
from aieos.domains.content.domain.version import ContentVersion


@dataclass(frozen=True, slots=True)
class AppendContentVersionCommand:
    expected_aggregate_revision: AggregateRevision
    version: ContentVersion
    provenance: Mapping[str, object] | None = None


@dataclass(frozen=True, slots=True)
class AppendContentVersionResult:
    content_id: ContentId
    version_id: ContentVersionId
    version_number: VersionNumber
    aggregate_revision: AggregateRevision


@dataclass(frozen=True, slots=True)
class LockedContentHead:
    """Aggregate head obtained under row lock for a single append transaction."""

    tenant_id: UUID
    content_id: ContentId
    aggregate_revision: AggregateRevision
    current_version_id: ContentVersionId | None
    current_version_number: VersionNumber | None
    published_version_id: ContentVersionId | None
    stewardship_state: str


@dataclass(frozen=True, slots=True)
class CreateContentCommand:
    """Client-settable Content metadata only."""

    content_type: str
    title: str
    description: str
    locale: str


@dataclass(frozen=True, slots=True)
class ContentReadModel:
    content_id: ContentId
    content_type: str
    title: str
    description: str
    locale: str
    stewardship_state: str
    current_version_id: ContentVersionId | None
    published_version_id: ContentVersionId | None
    aggregate_revision: AggregateRevision
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None


@dataclass(frozen=True, slots=True)
class ListContentsQuery:
    limit: int
    after_created_at: datetime | None = None
    after_content_id: ContentId | None = None


@dataclass(frozen=True, slots=True)
class ListContentsResult:
    items: tuple[ContentReadModel, ...]
    has_more: bool


def content_read_model(content: Content) -> ContentReadModel:
    return ContentReadModel(
        content_id=content.content_id,
        content_type=content.content_type.value,
        title=content.title,
        description=content.description,
        locale=content.locale,
        stewardship_state=content.stewardship_state.value,
        current_version_id=content.current_version_id,
        published_version_id=content.published_version_id,
        aggregate_revision=content.aggregate_revision,
        created_at=content.created_at,
        updated_at=content.updated_at,
        archived_at=content.archived_at,
    )

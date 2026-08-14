"""Teacher OS Review Queue application read models and queries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping

from aieos.domains.content.domain.identities import (
    AggregateRevision,
    ContentId,
    ContentVersionId,
    VersionNumber,
)

ARTIFACT_STATUS_IN_REVIEW = "In Review"


@dataclass(frozen=True, slots=True)
class TeacherReviewQueueItem:
    content_id: ContentId
    version_id: ContentVersionId
    version_number: VersionNumber
    content_type: str
    title: str
    description: str
    locale: str
    artifact_status: str
    origin: str
    aggregate_revision: AggregateRevision
    submitted_at: datetime
    version_created_at: datetime
    published_version_id: ContentVersionId | None


@dataclass(frozen=True, slots=True)
class TeacherReviewQueueDetail:
    content_id: ContentId
    version_id: ContentVersionId
    version_number: VersionNumber
    content_type: str
    title: str
    description: str
    locale: str
    artifact_status: str
    origin: str
    aggregate_revision: AggregateRevision
    submitted_at: datetime
    version_created_at: datetime
    published_version_id: ContentVersionId | None
    schema_id: str
    schema_version: int
    payload: Mapping[str, object]
    payload_sha256: str


@dataclass(frozen=True, slots=True)
class ListTeacherReviewQueueQuery:
    limit: int
    after_submitted_at: datetime | None = None
    after_content_id: ContentId | None = None


@dataclass(frozen=True, slots=True)
class TeacherReviewQueuePage:
    items: tuple[TeacherReviewQueueItem, ...]
    has_more: bool

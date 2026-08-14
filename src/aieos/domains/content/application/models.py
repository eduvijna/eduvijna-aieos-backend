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
    PublicationId,
    ReviewDecisionId,
    VersionNumber,
)
from aieos.domains.content.domain.version import ContentVersion
from aieos.domains.content.domain.version_asset_ref import VersionAssetRef
from aieos.domains.content.domain.provenance import AIGenerationProvenanceV1
from aieos.platform.resources import ResourceRef


@dataclass(frozen=True, slots=True)
class AppendContentVersionCommand:
    expected_aggregate_revision: AggregateRevision
    version: ContentVersion
    provenance: AIGenerationProvenanceV1 | Mapping[str, object] | None = None
    asset_refs: tuple[VersionAssetRef, ...] = ()


@dataclass(frozen=True, slots=True)
class VersionAssetAssociationSpec:
    """Framework-neutral generated Asset association before version identity allocation."""

    resource_ref: ResourceRef
    role: str
    ordinal: int
    required: bool


@dataclass(frozen=True, slots=True)
class AIGeneratedVersionMaterializationCommand:
    """Completed AI generation result ready for Content materialization."""

    content_id: ContentId
    expected_aggregate_revision: AggregateRevision
    schema_id: str
    schema_version: int
    payload: Mapping[str, object]
    provenance: AIGenerationProvenanceV1
    asset_refs: tuple[VersionAssetAssociationSpec, ...] = ()


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
    content_type: str


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


@dataclass(frozen=True, slots=True)
class ContentVersionReadModel:
    version_id: ContentVersionId
    content_id: ContentId
    version_number: VersionNumber
    parent_version_id: ContentVersionId | None
    schema_id: str
    schema_version: int
    payload: Mapping[str, object]
    payload_sha256: str
    origin: str
    created_at: datetime


def content_version_read_model(version: ContentVersion) -> ContentVersionReadModel:
    from aieos.domains.content.domain.version import thaw_json_value

    thawed = thaw_json_value(version.payload.body)
    if not isinstance(thawed, Mapping):
        raise TypeError("ContentVersion payload must thaw to a JSON object")
    return ContentVersionReadModel(
        version_id=version.version_id,
        content_id=version.content_id,
        version_number=version.version_number,
        parent_version_id=version.parent_version_id,
        schema_id=str(version.schema_id),
        schema_version=int(version.schema_version),
        payload=thawed,
        payload_sha256=version.payload.sha256.value,
        origin=version.origin.value,
        created_at=version.created_at,
    )


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


@dataclass(frozen=True, slots=True)
class ReviewSubmissionResult:
    content_id: ContentId
    version_id: ContentVersionId
    stewardship_state: str
    aggregate_revision: AggregateRevision


@dataclass(frozen=True, slots=True)
class ReviewDecisionResult:
    review_decision_id: ReviewDecisionId
    content_id: ContentId
    version_id: ContentVersionId
    decision: str
    reason_code: str | None
    comment: str | None
    decided_at: datetime
    stewardship_state: str
    aggregate_revision: AggregateRevision


@dataclass(frozen=True, slots=True)
class PublicationResult:
    publication_id: PublicationId
    content_id: ContentId
    version_id: ContentVersionId
    approval_decision_id: ReviewDecisionId
    published_at: datetime
    published_version_id: ContentVersionId
    aggregate_revision: AggregateRevision

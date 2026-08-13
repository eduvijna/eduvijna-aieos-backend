"""Application command/result contracts for immutable version append."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping
from uuid import UUID

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

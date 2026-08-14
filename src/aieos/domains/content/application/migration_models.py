"""Canonical migration candidate and durable migration-record models (GCI-I13)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping
from uuid import UUID

from aieos.domains.content.application.models import VersionAssetAssociationSpec
from aieos.domains.content.domain.identities import ContentId, ContentVersionId
from aieos.domains.content.domain.migration import MigrationSourceIdentity

MIGRATION_OUTCOME_FAILED = "FAILED"
MIGRATION_OUTCOME_IMPORTED = "IMPORTED"


@dataclass(frozen=True, slots=True)
class MigrationContentCandidate:
    """Canonical adapter output. No legacy trust or target identity fields."""

    source_identity: MigrationSourceIdentity
    source_version: str | None
    source_digest_sha256: str
    migration_batch_id: UUID
    mapping_id: str
    mapping_version: int
    target_owner_principal_id: UUID
    content_type: str
    title: str
    description: str
    locale: str
    schema_id: str
    schema_version: int
    payload: Mapping[str, object]
    asset_refs: tuple[VersionAssetAssociationSpec, ...] = ()


@dataclass(frozen=True, slots=True)
class ImportMigratedContentResult:
    content_id: ContentId
    version_id: ContentVersionId
    replayed: bool


@dataclass(frozen=True, slots=True)
class MigrationImportRecord:
    tenant_id: UUID
    source_system: str
    source_resource_type: str
    source_resource_id: str
    source_version: str | None
    source_digest_sha256: str
    mapping_id: str
    mapping_version: int
    first_migration_batch_id: UUID
    last_migration_batch_id: UUID
    outcome: str
    target_content_id: UUID | None
    target_version_id: UUID | None
    attempt_count: int
    first_attempt_at: datetime
    last_attempt_at: datetime
    completed_at: datetime | None
    failure_code: str | None

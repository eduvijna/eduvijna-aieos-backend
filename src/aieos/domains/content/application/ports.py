"""Persistence ports. Infrastructure types are not part of this contract."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Mapping, Protocol
from uuid import UUID

from aieos.domains.content.application.migration_models import MigrationImportRecord
from aieos.domains.content.application.models import LockedContentHead
from aieos.domains.content.application.review_queue_models import (
    TeacherReviewQueueDetail,
    TeacherReviewQueueItem,
)
from aieos.domains.content.domain.content import Content
from aieos.domains.content.domain.identities import (
    AggregateRevision,
    ContentId,
    ContentVersionId,
    PublicationId,
    ReviewDecisionId,
)
from aieos.domains.content.domain.publication import Publication
from aieos.domains.content.domain.review import ReviewDecision
from aieos.domains.content.domain.version import ContentVersion
from aieos.domains.content.domain.version_asset_ref import VersionAssetRef
from aieos.platform.events.ports import OutboxRepository
from aieos.platform.idempotency.ports import IdempotencyRepository
from aieos.platform.resources import ResourceRef
from aieos.platform.security.audit.ports import SecurityMutationAuditRepository
from aieos.platform.workflows.ports import WorkflowIntentRepository

CONTENT_REVIEW_SUBMIT = "content.review.submit"
CONTENT_REVIEW_DECIDE = "content.review.decide"
CONTENT_PUBLISH = "content.publish"
CONTENT_VERSION_CREATE = "content.version.create"
CONTENT_MIGRATE_IMPORT = "content.migrate.import"


class ContentVersionRepository(Protocol):
    """INSERT/READ persistence for immutable ContentVersion rows."""

    def insert(
        self,
        version: ContentVersion,
        provenance: Mapping[str, object] | None,
    ) -> None: ...

    def get(self, version_id: ContentVersionId) -> ContentVersion | None: ...

    def get_provenance(
        self, version_id: ContentVersionId
    ) -> Mapping[str, object] | None: ...


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


class PublicationAuthorizationPort(Protocol):
    """Current capability check for content.publish. Does not own users/roles/JWT."""

    def authorize(
        self,
        *,
        tenant_id: UUID,
        principal_id: UUID,
        content_id: ContentId,
        version_id: ContentVersionId,
        capability: str,
    ) -> None: ...


class PublicationGovernancePort(Protocol):
    """Local publication-governance gate. No network I/O."""

    def evaluate(
        self,
        *,
        tenant_id: UUID,
        content_id: ContentId,
        version_id: ContentVersionId,
    ) -> None: ...


class AssetReferenceValidationPort(Protocol):
    """Binding-time validation that a ResourceRef may be associated to Content."""

    def validate_binding(
        self, *, tenant_id: UUID, principal_id: UUID, resource_ref: ResourceRef
    ) -> None: ...


class AssetCurrentGovernancePort(Protocol):
    """Current-use governance for stored VersionAssetRef associations at publish."""

    def validate_current_use(
        self,
        *,
        tenant_id: UUID,
        principal_id: UUID,
        content_id: ContentId,
        version_id: ContentVersionId,
        asset_refs: Sequence[VersionAssetRef],
    ) -> None: ...


class AIGenerationAuthorizationPort(Protocol):
    """Current capability check for content.version.create on AI materialization."""

    def authorize(
        self,
        *,
        tenant_id: UUID,
        principal_id: UUID,
        content_id: ContentId,
        capability: str,
    ) -> None: ...


class MigrationSourceSerializationGate(Protocol):
    """Durable-safe source serialization spanning target attempt + FAILED finalization."""

    def hold(
        self,
        execution_tenant_id: UUID,
        source_system: str,
        source_resource_type: str,
        source_resource_id: str,
    ): ...


class ContentMigrationAuthorizationPort(Protocol):
    """Current capability check for content.migrate.import."""

    def authorize(
        self,
        *,
        tenant_id: UUID,
        principal_id: UUID,
        capability: str,
    ) -> None: ...


class MigrationImportRecordRepository(Protocol):
    """Durable migration source→target evidence. Not Content aggregate state."""

    def get(
        self,
        source_system: str,
        source_resource_type: str,
        source_resource_id: str,
    ) -> MigrationImportRecord | None: ...

    def insert_imported(self, record: MigrationImportRecord) -> None: ...

    def insert_failed(self, record: MigrationImportRecord) -> None: ...

    def mark_imported_from_failed(
        self,
        prior: MigrationImportRecord,
        *,
        target_content_id: UUID,
        target_version_id: UUID,
        migration_batch_id: UUID,
        completed_at: datetime,
    ) -> None: ...

    def update_failed_retry(
        self,
        prior: MigrationImportRecord,
        *,
        migration_batch_id: UUID,
        failure_code: str,
        attempted_at: datetime,
    ) -> None: ...


class VersionAssetRefRepository(Protocol):
    def insert_many(self, refs: Sequence[VersionAssetRef]) -> None: ...

    def list_for_version(
        self, content_id: ContentId, version_id: ContentVersionId
    ) -> list[VersionAssetRef]: ...


class ReviewQueueReadRepository(Protocol):
    """Read-only Teacher OS Review Queue projection. No enqueue/dequeue."""

    def list_page(
        self,
        *,
        limit: int,
        after_submitted_at: datetime | None,
        after_content_id: ContentId | None,
    ) -> list[TeacherReviewQueueItem]: ...

    def get_item(
        self, content_id: ContentId, version_id: ContentVersionId
    ) -> TeacherReviewQueueDetail | None: ...


class PublicationRepository(Protocol):
    """INSERT/READ persistence for immutable Publication rows."""

    def insert(self, publication: Publication) -> None: ...

    def get(self, publication_id: PublicationId) -> Publication | None: ...

    def get_for_version(
        self, content_id: ContentId, version_id: ContentVersionId
    ) -> Publication | None: ...


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
        expected_state: str,
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

    def set_published_version(
        self,
        *,
        content_id: ContentId,
        tenant_id: UUID,
        version_id: ContentVersionId,
        expected_revision: AggregateRevision,
        updated_at: datetime,
    ) -> AggregateRevision | None: ...


class ContentUnitOfWork(Protocol):
    contents: ContentRepository
    versions: ContentVersionRepository
    reviews: ReviewRepository
    publications: PublicationRepository
    version_asset_refs: VersionAssetRefRepository
    review_queue: ReviewQueueReadRepository
    migration_imports: MigrationImportRecordRepository
    idempotency: IdempotencyRepository
    workflow_intents: WorkflowIntentRepository
    outbox: OutboxRepository
    audit: SecurityMutationAuditRepository

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

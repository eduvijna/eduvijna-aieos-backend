"""Atomic AI Content create + materialize + submit-for-review in one Content UoW."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from aieos.domains.content.application.audit import (
    MutationAuditProvenance,
    content_version_ref,
    insert_required_content_audit,
)
from aieos.domains.content.application.in_uow import (
    create_content_in_uow,
    materialize_ai_version_in_uow,
)
from aieos.domains.content.application.models import (
    AIGeneratedVersionMaterializationCommand,
    CreateContentCommand,
)
from aieos.domains.content.application.ports import (
    CONTENT_VERSION_CREATE,
    AIGenerationAuthorizationPort,
    AssetReferenceValidationPort,
    ContentTypeCatalog,
    ContentUnitOfWorkFactory,
)
from aieos.domains.content.application.review import submit_for_review_in_uow
from aieos.domains.content.domain.identities import AggregateRevision, ContentId, ContentVersionId
from aieos.domains.content.domain.provenance import AIGenerationProvenanceV1
from aieos.domains.content.domain.schema import ContentSchemaRegistry
from aieos.domains.content.domain.states import StewardshipState
from aieos.domains.content.application.errors import UnknownContentType
from aieos.platform.events.models import MutationEventContext
from aieos.platform.security.audit import SecurityAuditAction


@dataclass(frozen=True, slots=True)
class CreateAIGeneratedContentForReviewCommand:
    content_type: str
    title: str
    description: str
    locale: str
    schema_id: str
    schema_version: int
    payload: dict[str, object]
    provenance: AIGenerationProvenanceV1


@dataclass(frozen=True, slots=True)
class CreateAIGeneratedContentForReviewResult:
    content_id: ContentId
    version_id: ContentVersionId
    content_type: str
    title: str
    stewardship_state: str
    aggregate_revision: AggregateRevision


class CreateAIGeneratedContentForReviewService:
    """Create Content, materialize AI version, submit for review — one transaction."""

    def __init__(
        self,
        uow_factory: ContentUnitOfWorkFactory,
        catalog: ContentTypeCatalog,
        schema_registry: ContentSchemaRegistry,
        asset_reference_validation: AssetReferenceValidationPort,
        ai_generation_authorization: AIGenerationAuthorizationPort,
    ) -> None:
        self._uow_factory = uow_factory
        self._catalog = catalog
        self._schema_registry = schema_registry
        self._asset_reference_validation = asset_reference_validation
        self._ai_generation_authorization = ai_generation_authorization

    def create(
        self,
        execution_tenant_id: UUID,
        principal_id: UUID,
        command: CreateAIGeneratedContentForReviewCommand,
        *,
        event_context: MutationEventContext,
        audit_provenance: MutationAuditProvenance,
        now: datetime | None = None,
    ) -> CreateAIGeneratedContentForReviewResult:
        if not self._catalog.contains(command.content_type):
            raise UnknownContentType("content_type is not registered")
        created_at = now if now is not None else datetime.now(UTC)

        with self._uow_factory(execution_tenant_id) as uow:
            content = create_content_in_uow(
                uow,
                execution_tenant_id,
                principal_id,
                CreateContentCommand(
                    content_type=command.content_type,
                    title=command.title,
                    description=command.description,
                    locale=command.locale,
                ),
                event_context=event_context,
                audit_provenance=audit_provenance,
                created_at=created_at,
            )
            self._ai_generation_authorization.authorize(
                tenant_id=execution_tenant_id,
                principal_id=principal_id,
                content_id=content.content_id,
                capability=CONTENT_VERSION_CREATE,
            )
            materialized = materialize_ai_version_in_uow(
                uow,
                execution_tenant_id,
                principal_id,
                AIGeneratedVersionMaterializationCommand(
                    content_id=content.content_id,
                    expected_aggregate_revision=AggregateRevision(0),
                    schema_id=command.schema_id,
                    schema_version=command.schema_version,
                    payload=command.payload,
                    provenance=command.provenance,
                    asset_refs=(),
                ),
                schema_registry=self._schema_registry,
                asset_reference_validation=self._asset_reference_validation,
                event_context=event_context,
                audit_provenance=audit_provenance,
                created_at=created_at,
            )
            revision = submit_for_review_in_uow(
                uow,
                execution_tenant_id,
                content_id=content.content_id,
                version_id=materialized.version_id,
                expected_aggregate_revision=materialized.aggregate_revision,
                event_context=event_context,
                updated_at=created_at,
            )
            insert_required_content_audit(
                uow,
                tenant_id=execution_tenant_id,
                action=SecurityAuditAction.CONTENT_REVIEW_SUBMIT,
                content_id=content.content_id.value,
                resource_revision_before=int(materialized.aggregate_revision),
                resource_revision_after=int(revision),
                related_resource_refs=(
                    content_version_ref(materialized.version_id.value),
                ),
                mutation_event_context=event_context,
                audit_provenance=audit_provenance,
                occurred_at=created_at,
            )
            uow.commit()

        return CreateAIGeneratedContentForReviewResult(
            content_id=content.content_id,
            version_id=materialized.version_id,
            content_type=command.content_type,
            title=command.title,
            stewardship_state=StewardshipState.IN_REVIEW.value,
            aggregate_revision=revision,
        )

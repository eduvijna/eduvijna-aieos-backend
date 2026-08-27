"""In-UoW Content mutation helpers shared by public services and composites."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from aieos.domains.content.application.asset_refs import (
    build_version_asset_refs,
    validate_asset_bindings,
)
from aieos.domains.content.application.audit import (
    MutationAuditProvenance,
    content_version_ref,
    insert_required_content_audit,
)
from aieos.domains.content.application.errors import (
    AIProvenanceInvalid,
    ContentNotFound,
    ContentPayloadInvalid,
    ContentSchemaMismatch,
    ContentSchemaNotFound,
    InvalidContentRequest,
    PersistenceInvariantViolation,
)
from aieos.domains.content.application.models import (
    AIGeneratedVersionMaterializationCommand,
    AppendContentVersionCommand,
    AppendContentVersionResult,
    CreateContentCommand,
)
from aieos.domains.content.application.ports import (
    AssetReferenceValidationPort,
    ContentUnitOfWork,
)
from aieos.domains.content.application.services import append_version_in_uow
from aieos.domains.content.domain.content import Content, ContentType
from aieos.domains.content.domain.errors import (
    ContentDomainError,
    InvalidPayloadError,
    SchemaNotFoundError,
)
from aieos.domains.content.domain.identities import (
    AggregateRevision,
    ContentId,
    ContentVersionId,
    VersionNumber,
)
from aieos.domains.content.domain.origin import ContentOrigin
from aieos.domains.content.domain.schema import ContentSchemaRegistry, SchemaId, SchemaVersion
from aieos.domains.content.domain.states import StewardshipState
from aieos.domains.content.domain.version import ContentPayload, ContentVersion
from aieos.platform.events.content_events import content_created_outbox
from aieos.platform.events.models import MutationEventContext
from aieos.platform.security.audit import SecurityAuditAction


def create_content_in_uow(
    uow: ContentUnitOfWork,
    execution_tenant_id: UUID,
    principal_id: UUID,
    command: CreateContentCommand,
    *,
    event_context: MutationEventContext,
    audit_provenance: MutationAuditProvenance,
    created_at: datetime,
) -> Content:
    """Insert Content + outbox + audit inside an open UoW. Caller owns commit."""
    try:
        content = Content(
            content_id=ContentId.generate(),
            tenant_id=execution_tenant_id,
            owner_principal_id=principal_id,
            content_type=ContentType(command.content_type),
            title=command.title,
            description=command.description,
            locale=command.locale,
            stewardship_state=StewardshipState.DRAFT,
            current_version_id=None,
            published_version_id=None,
            aggregate_revision=AggregateRevision(0),
            created_at=created_at,
            created_by_principal_id=principal_id,
            updated_at=created_at,
            archived_at=None,
        )
    except ContentDomainError as exc:
        raise InvalidContentRequest("content create request is invalid") from exc
    uow.contents.insert(content)
    uow.outbox.insert(
        content_created_outbox(
            tenant_id=execution_tenant_id,
            content_id=content.content_id.value,
            content_type=command.content_type,
            context=event_context,
            created_at=created_at,
        )
    )
    insert_required_content_audit(
        uow,
        tenant_id=execution_tenant_id,
        action=SecurityAuditAction.CONTENT_CREATE,
        content_id=content.content_id.value,
        resource_revision_before=None,
        resource_revision_after=0,
        related_resource_refs=(),
        mutation_event_context=event_context,
        audit_provenance=audit_provenance,
        occurred_at=created_at,
    )
    return content


def materialize_ai_version_in_uow(
    uow: ContentUnitOfWork,
    execution_tenant_id: UUID,
    principal_id: UUID,
    command: AIGeneratedVersionMaterializationCommand,
    *,
    schema_registry: ContentSchemaRegistry,
    asset_reference_validation: AssetReferenceValidationPort,
    event_context: MutationEventContext,
    audit_provenance: MutationAuditProvenance,
    created_at: datetime,
) -> AppendContentVersionResult:
    """Materialize an AI ContentVersion inside an open UoW. Caller owns commit."""
    if command.provenance.correlation_id != event_context.correlation_id:
        raise AIProvenanceInvalid(
            "provenance.correlation_id must equal MutationEventContext.correlation_id"
        )
    head = uow.contents.get_head_for_update(command.content_id)
    if head is None or head.tenant_id != execution_tenant_id:
        raise ContentNotFound("Content is not visible in the execution tenant")
    try:
        registered = schema_registry.get(command.schema_id, command.schema_version)
    except SchemaNotFoundError as exc:
        raise ContentSchemaNotFound("schema is not registered") from exc
    if registered.content_type != head.content_type:
        raise ContentSchemaMismatch(
            "registered schema content_type does not match Content"
        )
    try:
        registered.validate(command.payload)
        domain_payload = ContentPayload.from_mapping(command.payload)
    except InvalidPayloadError as exc:
        raise ContentPayloadInvalid("payload failed schema validation") from exc

    if head.current_version_id is None:
        version_number = VersionNumber(1)
        parent = None
    else:
        if head.current_version_number is None:
            raise PersistenceInvariantViolation(
                "locked head is missing current version_number"
            )
        version_number = VersionNumber(head.current_version_number.value + 1)
        parent = head.current_version_id

    version = ContentVersion(
        version_id=ContentVersionId.generate(),
        tenant_id=execution_tenant_id,
        content_id=command.content_id,
        version_number=version_number,
        parent_version_id=parent,
        schema_id=SchemaId(command.schema_id),
        schema_version=SchemaVersion(command.schema_version),
        payload=domain_payload,
        origin=ContentOrigin.AI,
        created_at=created_at,
        created_by_principal_id=principal_id,
    )
    association_items = [
        {
            "resource_ref": {
                "resource_type": spec.resource_ref.resource_type,
                "resource_id": spec.resource_ref.resource_id,
                "resource_revision": spec.resource_ref.resource_revision,
            },
            "role": spec.role,
            "ordinal": spec.ordinal,
            "required": spec.required,
        }
        for spec in command.asset_refs
    ]
    built_refs = build_version_asset_refs(
        tenant_id=execution_tenant_id,
        content_id=command.content_id,
        version_id=version.version_id,
        created_at=created_at,
        items=association_items,
    )
    if built_refs:
        validate_asset_bindings(
            asset_reference_validation,
            execution_tenant_id,
            principal_id,
            tuple(ref.resource_ref for ref in built_refs),
        )
    result = append_version_in_uow(
        uow,
        execution_tenant_id,
        AppendContentVersionCommand(
            expected_aggregate_revision=command.expected_aggregate_revision,
            version=version,
            provenance=command.provenance,
            asset_refs=built_refs,
        ),
        now=created_at,
        event_context=event_context,
        head=head,
    )
    insert_required_content_audit(
        uow,
        tenant_id=execution_tenant_id,
        action=SecurityAuditAction.CONTENT_AI_MATERIALIZE,
        content_id=command.content_id.value,
        resource_revision_before=int(command.expected_aggregate_revision),
        resource_revision_after=int(result.aggregate_revision),
        related_resource_refs=(content_version_ref(version.version_id.value),),
        mutation_event_context=event_context,
        audit_provenance=audit_provenance,
        occurred_at=created_at,
    )
    return result

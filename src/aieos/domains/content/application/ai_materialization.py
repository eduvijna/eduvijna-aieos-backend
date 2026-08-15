"""Inbound AI-generated ContentVersion materialization. No AI provider calls."""

from __future__ import annotations

from datetime import UTC, datetime
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
    PersistenceInvariantViolation,
)
from aieos.domains.content.application.models import (
    AIGeneratedVersionMaterializationCommand,
    AppendContentVersionCommand,
    AppendContentVersionResult,
)
from aieos.domains.content.application.ports import (
    CONTENT_VERSION_CREATE,
    AIGenerationAuthorizationPort,
    AssetReferenceValidationPort,
    ContentUnitOfWorkFactory,
)
from aieos.domains.content.application.services import append_version_in_uow
from aieos.domains.content.domain.errors import InvalidPayloadError, SchemaNotFoundError
from aieos.domains.content.domain.identities import ContentVersionId, VersionNumber
from aieos.domains.content.domain.origin import ContentOrigin
from aieos.domains.content.domain.schema import ContentSchemaRegistry, SchemaId, SchemaVersion
from aieos.domains.content.domain.version import ContentPayload, ContentVersion
from aieos.platform.events.models import MutationEventContext
from aieos.platform.security.audit import SecurityAuditAction


class MaterializeAIGeneratedContentVersionService:
    """Persist a completed AI generation result as an authoritative ContentVersion."""

    def __init__(
        self,
        uow_factory: ContentUnitOfWorkFactory,
        schema_registry: ContentSchemaRegistry,
        asset_reference_validation: AssetReferenceValidationPort,
        ai_generation_authorization: AIGenerationAuthorizationPort,
    ) -> None:
        self._uow_factory = uow_factory
        self._schema_registry = schema_registry
        self._asset_reference_validation = asset_reference_validation
        self._ai_generation_authorization = ai_generation_authorization

    def materialize(
        self,
        execution_tenant_id: UUID,
        principal_id: UUID,
        command: AIGeneratedVersionMaterializationCommand,
        *,
        event_context: MutationEventContext,
        audit_provenance: MutationAuditProvenance,
        now: datetime | None = None,
    ) -> AppendContentVersionResult:
        created_at = now if now is not None else datetime.now(UTC)
        if command.provenance.correlation_id != event_context.correlation_id:
            raise AIProvenanceInvalid(
                "provenance.correlation_id must equal MutationEventContext.correlation_id"
            )
        self._ai_generation_authorization.authorize(
            tenant_id=execution_tenant_id,
            principal_id=principal_id,
            content_id=command.content_id,
            capability=CONTENT_VERSION_CREATE,
        )

        with self._uow_factory(execution_tenant_id) as uow:
            head = uow.contents.get_head_for_update(command.content_id)
            if head is None or head.tenant_id != execution_tenant_id:
                raise ContentNotFound("Content is not visible in the execution tenant")
            try:
                registered = self._schema_registry.get(
                    command.schema_id, command.schema_version
                )
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
                    self._asset_reference_validation,
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
            uow.commit()
            return result

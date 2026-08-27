"""Inbound AI-generated ContentVersion materialization. No AI provider calls."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from aieos.domains.content.application.audit import MutationAuditProvenance
from aieos.domains.content.application.errors import AIProvenanceInvalid
from aieos.domains.content.application.in_uow import materialize_ai_version_in_uow
from aieos.domains.content.application.models import (
    AIGeneratedVersionMaterializationCommand,
    AppendContentVersionResult,
)
from aieos.domains.content.application.ports import (
    CONTENT_VERSION_CREATE,
    AIGenerationAuthorizationPort,
    AssetReferenceValidationPort,
    ContentUnitOfWorkFactory,
)
from aieos.domains.content.domain.schema import ContentSchemaRegistry
from aieos.platform.events.models import MutationEventContext


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
            result = materialize_ai_version_in_uow(
                uow,
                execution_tenant_id,
                principal_id,
                command,
                schema_registry=self._schema_registry,
                asset_reference_validation=self._asset_reference_validation,
                event_context=event_context,
                audit_provenance=audit_provenance,
                created_at=created_at,
            )
            uow.commit()
            return result

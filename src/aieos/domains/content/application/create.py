"""Create one Content aggregate. Does not create ContentVersion v1."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from aieos.domains.content.application.audit import MutationAuditProvenance
from aieos.domains.content.application.errors import (
    IdempotencyKeyReused,
    PersistenceInvariantViolation,
    UnknownContentType,
)
from aieos.domains.content.application.in_uow import create_content_in_uow
from aieos.domains.content.application.models import (
    ContentReadModel,
    CreateContentCommand,
    content_read_model,
)
from aieos.domains.content.application.ports import ContentTypeCatalog, ContentUnitOfWorkFactory
from aieos.domains.content.domain.identities import AggregateRevision, ContentId
from aieos.domains.content.domain.states import StewardshipState
from aieos.platform.events.models import MutationEventContext
from aieos.platform.idempotency.hashing import fingerprint_material, hash_idempotency_key
from aieos.platform.idempotency.models import CONTENT_CREATE_V1, IdempotencyOutcome, IdempotencyScope


def _create_fingerprint(command: CreateContentCommand) -> str:
    return fingerprint_material(
        {
            "content_type": command.content_type,
            "title": command.title,
            "description": command.description,
            "locale": command.locale,
        }
    )


def original_create_read_model(
    command: CreateContentCommand,
    outcome: IdempotencyOutcome,
) -> ContentReadModel:
    """Reconstruct the established create result. Not current Content state."""
    return ContentReadModel(
        content_id=ContentId(outcome.result_content_id),
        content_type=command.content_type,
        title=command.title,
        description=command.description,
        locale=command.locale,
        stewardship_state=StewardshipState.DRAFT.value,
        current_version_id=None,
        published_version_id=None,
        aggregate_revision=AggregateRevision(outcome.result_aggregate_revision),
        created_at=outcome.created_at,
        updated_at=outcome.created_at,
        archived_at=None,
    )


class CreateContentService:
    """Authoritative Content insert. Development/test foundation only."""

    def __init__(
        self,
        uow_factory: ContentUnitOfWorkFactory,
        catalog: ContentTypeCatalog,
        *,
        idempotency_retention: timedelta,
    ) -> None:
        if idempotency_retention.total_seconds() <= 0:
            raise ValueError("idempotency_retention must be a positive duration")
        self._uow_factory = uow_factory
        self._catalog = catalog
        self._idempotency_retention = idempotency_retention

    def create(
        self,
        execution_tenant_id: UUID,
        principal_id: UUID,
        command: CreateContentCommand,
        *,
        idempotency_key: str,
        event_context: MutationEventContext,
        audit_provenance: MutationAuditProvenance,
        now: datetime | None = None,
    ) -> ContentReadModel:
        fingerprint = _create_fingerprint(command)
        scope = IdempotencyScope(
            tenant_id=execution_tenant_id,
            principal_id=principal_id,
            operation=CONTENT_CREATE_V1,
            key_sha256=hash_idempotency_key(idempotency_key),
        )
        with self._uow_factory(execution_tenant_id) as uow:
            uow.idempotency.acquire_scope(scope)
            existing = uow.idempotency.get(scope)
            if existing is not None:
                if existing.request_fingerprint_sha256 != fingerprint:
                    raise IdempotencyKeyReused("idempotency key already bound")
                found = uow.contents.get(ContentId(existing.result_content_id))
                if found is None:
                    raise PersistenceInvariantViolation(
                        "idempotent create outcome is not visible"
                    )
                return original_create_read_model(command, existing)

            if not self._catalog.contains(command.content_type):
                raise UnknownContentType("content_type is not registered")
            created_at = now if now is not None else datetime.now(UTC)
            content = create_content_in_uow(
                uow,
                execution_tenant_id,
                principal_id,
                command,
                event_context=event_context,
                audit_provenance=audit_provenance,
                created_at=created_at,
            )
            uow.idempotency.insert(
                IdempotencyOutcome(
                    tenant_id=scope.tenant_id,
                    principal_id=scope.principal_id,
                    operation=scope.operation,
                    key_sha256=scope.key_sha256,
                    request_fingerprint_sha256=fingerprint,
                    result_content_id=content.content_id.value,
                    result_version_id=None,
                    result_review_decision_id=None,
                    result_publication_id=None,
                    result_aggregate_revision=0,
                    created_at=created_at,
                    expires_at=created_at + self._idempotency_retention,
                )
            )
            uow.commit()
        return content_read_model(content)

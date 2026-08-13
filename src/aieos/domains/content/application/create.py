"""Create one Content aggregate. Does not create ContentVersion v1."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from aieos.domains.content.application.errors import (
    IdempotencyKeyReused,
    InvalidContentRequest,
    PersistenceInvariantViolation,
    UnknownContentType,
)
from aieos.domains.content.application.models import (
    ContentReadModel,
    CreateContentCommand,
    content_read_model,
)
from aieos.domains.content.application.ports import ContentTypeCatalog, ContentUnitOfWorkFactory
from aieos.domains.content.domain.content import Content, ContentType
from aieos.domains.content.domain.errors import ContentDomainError
from aieos.domains.content.domain.identities import AggregateRevision, ContentId
from aieos.domains.content.domain.states import StewardshipState
from aieos.platform.idempotency.hashing import fingerprint_material, hash_idempotency_key
from aieos.platform.idempotency.models import CONTENT_CREATE_V1, IdempotencyOutcome, IdempotencyScope


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
        now: datetime | None = None,
    ) -> ContentReadModel:
        if not self._catalog.contains(command.content_type):
            raise UnknownContentType("content_type is not registered")
        created_at = now if now is not None else datetime.now(UTC)
        fingerprint = fingerprint_material(
            {
                "content_type": command.content_type,
                "title": command.title,
                "description": command.description,
                "locale": command.locale,
            }
        )
        scope = IdempotencyScope(
            tenant_id=execution_tenant_id,
            principal_id=principal_id,
            operation=CONTENT_CREATE_V1,
            key_sha256=hash_idempotency_key(idempotency_key),
        )
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
                return content_read_model(found)
            uow.contents.insert(content)
            uow.idempotency.insert(
                IdempotencyOutcome(
                    tenant_id=scope.tenant_id,
                    principal_id=scope.principal_id,
                    operation=scope.operation,
                    key_sha256=scope.key_sha256,
                    request_fingerprint_sha256=fingerprint,
                    result_content_id=content.content_id.value,
                    result_version_id=None,
                    result_aggregate_revision=0,
                    created_at=created_at,
                    expires_at=created_at + self._idempotency_retention,
                )
            )
            uow.commit()
        return content_read_model(content)

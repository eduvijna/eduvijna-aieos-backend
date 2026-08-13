"""Create one Content aggregate. Does not create ContentVersion v1."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from aieos.domains.content.application.errors import (
    InvalidContentRequest,
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


class CreateContentService:
    """Authoritative Content insert. Development/test foundation only."""

    def __init__(
        self,
        uow_factory: ContentUnitOfWorkFactory,
        catalog: ContentTypeCatalog,
    ) -> None:
        self._uow_factory = uow_factory
        self._catalog = catalog

    def create(
        self,
        execution_tenant_id: UUID,
        principal_id: UUID,
        command: CreateContentCommand,
        *,
        now: datetime | None = None,
    ) -> ContentReadModel:
        if not self._catalog.contains(command.content_type):
            raise UnknownContentType("content_type is not registered")
        created_at = now if now is not None else datetime.now(UTC)
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
            uow.contents.insert(content)
            uow.commit()
        return content_read_model(content)

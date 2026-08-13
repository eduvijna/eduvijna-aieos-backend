"""Content insert unique violations must not map to VersionAlreadyExists."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from psycopg.errors import UniqueViolation
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from aieos.domains.content.application.errors import (
    ContentAlreadyExists,
    VersionAlreadyExists,
)
from aieos.domains.content.domain.content import Content, ContentType
from aieos.domains.content.domain.identities import AggregateRevision, ContentId
from aieos.domains.content.domain.states import StewardshipState
from aieos.domains.content.infrastructure.persistence.uow import (
    SqlAlchemyContentUnitOfWorkFactory,
)

pytestmark = pytest.mark.gci_i04

FIXED_NOW = datetime(2026, 8, 13, 20, 0, tzinfo=UTC)


def _content(tenant_id: uuid.UUID) -> Content:
    principal = uuid.uuid7()
    return Content(
        content_id=ContentId.generate(),
        tenant_id=tenant_id,
        owner_principal_id=principal,
        content_type=ContentType("test.generic"),
        title="Title",
        description="Description",
        locale="en-IN",
        stewardship_state=StewardshipState.DRAFT,
        current_version_id=None,
        published_version_id=None,
        aggregate_revision=AggregateRevision(0),
        created_at=FIXED_NOW,
        created_by_principal_id=principal,
        updated_at=FIXED_NOW,
        archived_at=None,
    )


def test_content_insert_unique_violation_is_not_version_already_exists(
    runtime_engine, monkeypatch
) -> None:
    tenant_id = uuid.uuid7()
    factory = SqlAlchemyContentUnitOfWorkFactory(runtime_engine)
    with factory(tenant_id) as uow:
        monkeypatch.setattr(
            uow._connection,
            "execute",
            lambda *a, **k: (_ for _ in ()).throw(
                IntegrityError("INSERT", {}, UniqueViolation("duplicate"))
            ),
        )
        with pytest.raises(ContentAlreadyExists) as caught:
            uow.contents.insert(_content(tenant_id))
        assert not isinstance(caught.value, VersionAlreadyExists)
        assert not isinstance(caught.value, SQLAlchemyError)
        uow.rollback()

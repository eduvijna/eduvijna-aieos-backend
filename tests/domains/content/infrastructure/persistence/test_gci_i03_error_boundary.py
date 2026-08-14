"""GCI-I03R1: infrastructure exceptions must not escape application ports."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from psycopg.errors import UniqueViolation
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError

from aieos.domains.content.application.errors import (
    AggregateRevisionConflict,
    ContentApplicationError,
    PersistenceOperationFailed,
    VersionAlreadyExists,
)
from aieos.domains.content.domain.identities import (
    AggregateRevision,
    ContentId,
    ContentVersionId,
    VersionNumber,
)
from aieos.domains.content.domain.origin import ContentOrigin
from aieos.domains.content.domain.schema import SchemaId, SchemaVersion
from aieos.domains.content.domain.version import ContentPayload, ContentVersion
from aieos.domains.content.infrastructure.persistence.uow import (
    SqlAlchemyContentUnitOfWorkFactory,
)

pytestmark = pytest.mark.gci_i03

FIXED_NOW = datetime(2026, 8, 13, 18, 30, tzinfo=UTC)


def _operational_error() -> OperationalError:
    return OperationalError("SELECT 1", {}, Exception("injected driver failure"))


def _assert_neutral_operation_failed(exc: BaseException) -> None:
    assert isinstance(exc, PersistenceOperationFailed)
    assert isinstance(exc, ContentApplicationError)
    assert not isinstance(exc, SQLAlchemyError)
    assert "sqlalchemy" not in type(exc).__module__
    assert "psycopg" not in type(exc).__module__


def _version(tenant_id: uuid.UUID, content_id: ContentId) -> ContentVersion:
    return ContentVersion(
        version_id=ContentVersionId.generate(),
        tenant_id=tenant_id,
        content_id=content_id,
        version_number=VersionNumber(1),
        parent_version_id=None,
        schema_id=SchemaId("test.generic"),
        schema_version=SchemaVersion(1),
        payload=ContentPayload.from_mapping({"marker": "err"}),
        origin=ContentOrigin.HUMAN,
        created_at=FIXED_NOW,
        created_by_principal_id=uuid.uuid7(),
    )


class TestRepositoryTranslation:
    def test_head_read_driver_failure_is_operation_failed(
        self, runtime_engine, monkeypatch
    ) -> None:
        tenant_id = uuid.uuid7()
        factory = SqlAlchemyContentUnitOfWorkFactory(runtime_engine)
        with factory(tenant_id) as uow:
            monkeypatch.setattr(
                uow._connection, "execute", lambda *a, **k: (_ for _ in ()).throw(_operational_error())
            )
            with pytest.raises(PersistenceOperationFailed) as caught:
                uow.contents.get_head_for_update(ContentId.generate())
            _assert_neutral_operation_failed(caught.value)
            uow.rollback()

    def test_version_get_and_insert_driver_failure_is_operation_failed(
        self, runtime_engine, monkeypatch
    ) -> None:
        tenant_id = uuid.uuid7()
        content_id = ContentId.generate()
        factory = SqlAlchemyContentUnitOfWorkFactory(runtime_engine)
        with factory(tenant_id) as uow:
            monkeypatch.setattr(
                uow._connection, "execute", lambda *a, **k: (_ for _ in ()).throw(_operational_error())
            )
            with pytest.raises(PersistenceOperationFailed) as caught_get:
                uow.versions.get(ContentVersionId.generate())
            _assert_neutral_operation_failed(caught_get.value)
            with pytest.raises(PersistenceOperationFailed) as caught_insert:
                uow.versions.insert(_version(tenant_id, content_id), None)
            _assert_neutral_operation_failed(caught_insert.value)
            uow.rollback()

    def test_insert_unique_violation_remains_version_already_exists(
        self, runtime_engine, monkeypatch
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
            with pytest.raises(VersionAlreadyExists) as caught:
                uow.versions.insert(_version(tenant_id, ContentId.generate()), None)
            assert not isinstance(caught.value, SQLAlchemyError)
            uow.rollback()

    def test_advance_driver_failure_is_operation_failed(
        self, runtime_engine, monkeypatch
    ) -> None:
        tenant_id = uuid.uuid7()
        factory = SqlAlchemyContentUnitOfWorkFactory(runtime_engine)
        with factory(tenant_id) as uow:
            monkeypatch.setattr(
                uow._connection, "execute", lambda *a, **k: (_ for _ in ()).throw(_operational_error())
            )
            with pytest.raises(PersistenceOperationFailed) as caught:
                uow.contents.advance_current_version(
                    content_id=ContentId.generate(),
                    tenant_id=tenant_id,
                    expected_revision=AggregateRevision(0),
                    expected_current_version_id=None,
                    expected_state="DRAFT",
                    new_version_id=ContentVersionId.generate(),
                    updated_at=FIXED_NOW,
                )
            _assert_neutral_operation_failed(caught.value)
            uow.rollback()

    def test_application_conflicts_are_not_translated(
        self, runtime_engine, monkeypatch
    ) -> None:
        tenant_id = uuid.uuid7()
        factory = SqlAlchemyContentUnitOfWorkFactory(runtime_engine)
        with factory(tenant_id) as uow:
            monkeypatch.setattr(
                uow._connection,
                "execute",
                lambda *a, **k: (_ for _ in ()).throw(
                    AggregateRevisionConflict("keep me")
                ),
            )
            with pytest.raises(AggregateRevisionConflict, match="keep me"):
                uow.contents.get_head_for_update(ContentId.generate())
            uow.rollback()


class TestUnitOfWorkTranslation:
    def test_tenant_context_setup_failure_is_operation_failed(
        self, runtime_engine, monkeypatch
    ) -> None:
        tenant_id = uuid.uuid7()
        factory = SqlAlchemyContentUnitOfWorkFactory(runtime_engine)
        real_connect = runtime_engine.connect

        def connect_wrapper():
            conn = real_connect()
            real_execute = conn.execute

            def execute(statement, *args, **kwargs):
                if "set_config" in str(statement):
                    raise _operational_error()
                return real_execute(statement, *args, **kwargs)

            conn.execute = execute  # type: ignore[method-assign]
            return conn

        monkeypatch.setattr(runtime_engine, "connect", connect_wrapper)
        with pytest.raises(PersistenceOperationFailed) as caught:
            with factory(tenant_id):
                pass
        _assert_neutral_operation_failed(caught.value)

    def test_commit_failure_is_operation_failed(self, runtime_engine, monkeypatch) -> None:
        tenant_id = uuid.uuid7()
        factory = SqlAlchemyContentUnitOfWorkFactory(runtime_engine)
        with factory(tenant_id) as uow:
            monkeypatch.setattr(
                type(uow._transaction),
                "commit",
                lambda self: (_ for _ in ()).throw(_operational_error()),
            )
            with pytest.raises(PersistenceOperationFailed) as caught:
                uow.commit()
            _assert_neutral_operation_failed(caught.value)

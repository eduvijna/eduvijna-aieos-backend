"""SQLAlchemy Unit of Work for Generic Content. Owns the transaction."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.engine import Transaction

from aieos.domains.content.infrastructure.persistence.repositories import (
    SqlAlchemyContentRepository,
    SqlAlchemyContentVersionRepository,
)


class SqlAlchemyContentUnitOfWork:
    def __init__(self, engine: Engine, execution_tenant_id: UUID) -> None:
        self._engine = engine
        self._execution_tenant_id = execution_tenant_id
        self._connection: Connection | None = None
        self._transaction: Transaction | None = None
        self.contents: SqlAlchemyContentRepository
        self.versions: SqlAlchemyContentVersionRepository

    def __enter__(self) -> SqlAlchemyContentUnitOfWork:
        self._connection = self._engine.connect()
        self._transaction = self._connection.begin()
        self._connection.execute(
            text("SELECT set_config('aieos.tenant_id', :tid, true)"),
            {"tid": str(self._execution_tenant_id)},
        )
        self.contents = SqlAlchemyContentRepository(
            self._connection, self._execution_tenant_id
        )
        self.versions = SqlAlchemyContentVersionRepository(self._connection)
        return self

    def commit(self) -> None:
        if self._transaction is None:
            raise RuntimeError("Content Unit of Work is not active")
        self._transaction.commit()

    def rollback(self) -> None:
        if self._transaction is not None and self._transaction.is_active:
            self._transaction.rollback()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object,
    ) -> None:
        try:
            if self._transaction is not None and self._transaction.is_active:
                self._transaction.rollback()
        finally:
            if self._connection is not None:
                self._connection.close()
            self._connection = None
            self._transaction = None


class SqlAlchemyContentUnitOfWorkFactory:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def __call__(self, execution_tenant_id: UUID) -> SqlAlchemyContentUnitOfWork:
        return SqlAlchemyContentUnitOfWork(self._engine, execution_tenant_id)

"""SQLAlchemy Unit of Work for AI GenerationRun. Owns the transaction."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine, Transaction

from aieos.platform.ai.application.errors import PersistenceOperationFailed
from aieos.platform.ai.infrastructure.persistence.errors import (
    reraise_as_application_error,
)
from aieos.platform.ai.infrastructure.persistence.repositories import (
    SqlAlchemyGenerationRunRepository,
)


class SqlAlchemyAIUnitOfWork:
    def __init__(self, engine: Engine, execution_tenant_id: UUID) -> None:
        self._engine = engine
        self._execution_tenant_id = execution_tenant_id
        self._connection: Connection | None = None
        self._transaction: Transaction | None = None
        self.generation_runs: SqlAlchemyGenerationRunRepository

    def __enter__(self) -> SqlAlchemyAIUnitOfWork:
        try:
            self._connection = self._engine.connect()
            self._transaction = self._connection.begin()
            self._connection.execute(
                text("SELECT set_config('aieos.tenant_id', :tid, true)"),
                {"tid": str(self._execution_tenant_id)},
            )
            self.generation_runs = SqlAlchemyGenerationRunRepository(
                self._connection, self._execution_tenant_id
            )
            return self
        except Exception as exc:
            self._cleanup(suppress=True)
            reraise_as_application_error(exc)

    def commit(self) -> None:
        if self._transaction is None:
            raise PersistenceOperationFailed("AI Unit of Work is not active")
        try:
            self._transaction.commit()
        except Exception as exc:
            reraise_as_application_error(exc)

    def rollback(self) -> None:
        if self._transaction is None or not self._transaction.is_active:
            return
        try:
            self._transaction.rollback()
        except Exception as exc:
            reraise_as_application_error(exc)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object,
    ) -> None:
        self._cleanup(suppress=exc_type is not None)

    def _cleanup(self, *, suppress: bool) -> None:
        try:
            if self._transaction is not None and self._transaction.is_active:
                try:
                    self._transaction.rollback()
                except Exception as rollback_exc:
                    if not suppress:
                        reraise_as_application_error(rollback_exc)
        finally:
            try:
                if self._connection is not None:
                    self._connection.close()
            except Exception as close_exc:
                if not suppress:
                    reraise_as_application_error(close_exc)
            finally:
                self._connection = None
                self._transaction = None


class SqlAlchemyAIUnitOfWorkFactory:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def __call__(self, execution_tenant_id: UUID) -> SqlAlchemyAIUnitOfWork:
        return SqlAlchemyAIUnitOfWork(self._engine, execution_tenant_id)

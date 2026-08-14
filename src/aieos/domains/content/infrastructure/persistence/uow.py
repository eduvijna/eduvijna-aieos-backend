"""SQLAlchemy Unit of Work for Generic Content. Owns the transaction."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.engine import Transaction

from aieos.domains.content.application.errors import PersistenceOperationFailed
from aieos.domains.content.infrastructure.persistence.errors import (
    reraise_as_application_error,
)
from aieos.domains.content.infrastructure.persistence.repositories import (
    SqlAlchemyContentRepository,
    SqlAlchemyContentVersionRepository,
    SqlAlchemyReviewDecisionRepository,
)
from aieos.platform.api.infrastructure.persistence.repositories import (
    SqlAlchemyIdempotencyRepository,
)
from aieos.platform.workflows.persistence.repositories import (
    SqlAlchemyWorkflowIntentRepository,
)


class SqlAlchemyContentUnitOfWork:
    def __init__(self, engine: Engine, execution_tenant_id: UUID) -> None:
        self._engine = engine
        self._execution_tenant_id = execution_tenant_id
        self._connection: Connection | None = None
        self._transaction: Transaction | None = None
        self.contents: SqlAlchemyContentRepository
        self.versions: SqlAlchemyContentVersionRepository
        self.reviews: SqlAlchemyReviewDecisionRepository
        self.idempotency: SqlAlchemyIdempotencyRepository
        self.workflow_intents: SqlAlchemyWorkflowIntentRepository

    def __enter__(self) -> SqlAlchemyContentUnitOfWork:
        try:
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
            self.reviews = SqlAlchemyReviewDecisionRepository(self._connection)
            self.idempotency = SqlAlchemyIdempotencyRepository(
                self._connection, self._execution_tenant_id
            )
            self.workflow_intents = SqlAlchemyWorkflowIntentRepository(self._connection)
            return self
        except Exception as exc:
            self._cleanup(suppress=True)
            reraise_as_application_error(exc)

    def commit(self) -> None:
        if self._transaction is None:
            raise PersistenceOperationFailed("Content Unit of Work is not active")
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


class SqlAlchemyContentUnitOfWorkFactory:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def __call__(self, execution_tenant_id: UUID) -> SqlAlchemyContentUnitOfWork:
        return SqlAlchemyContentUnitOfWork(self._engine, execution_tenant_id)

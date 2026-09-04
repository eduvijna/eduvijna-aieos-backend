"""SQLAlchemy Unit of Work for Assessment. Owns the transaction."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine, Transaction

from aieos.domains.assessment.application.errors import PersistenceOperationFailed
from aieos.domains.assessment.infrastructure.persistence.audit_repository import (
    AssessmentSecurityMutationAuditRepository,
)
from aieos.domains.assessment.infrastructure.persistence.content_authority import (
    SqlAlchemyAssessmentContentAuthorityAdapter,
)
from aieos.domains.assessment.infrastructure.persistence.errors import (
    reraise_as_application_error,
)
from aieos.domains.assessment.infrastructure.persistence.repositories import (
    SqlAlchemyClassroomAssessmentRepository,
)
from aieos.domains.assessment.infrastructure.persistence.teaching_composition import (
    SqlAlchemyAssessmentTeachingCompositionAdapter,
)
from aieos.platform.api.infrastructure.persistence.repositories import (
    SqlAlchemyIdempotencyRepository,
)


class SqlAlchemyAssessmentUnitOfWork:
    def __init__(self, engine: Engine, execution_tenant_id: UUID) -> None:
        self._engine = engine
        self._execution_tenant_id = execution_tenant_id
        self._connection: Connection | None = None
        self._transaction: Transaction | None = None
        self.classroom_assessments: SqlAlchemyClassroomAssessmentRepository
        self.idempotency: SqlAlchemyIdempotencyRepository
        self.audit: AssessmentSecurityMutationAuditRepository
        self.content_authority: SqlAlchemyAssessmentContentAuthorityAdapter
        self.teaching_composition: SqlAlchemyAssessmentTeachingCompositionAdapter

    def __enter__(self) -> SqlAlchemyAssessmentUnitOfWork:
        try:
            self._connection = self._engine.connect()
            self._transaction = self._connection.begin()
            self._connection.execute(
                text("SELECT set_config('aieos.tenant_id', :tid, true)"),
                {"tid": str(self._execution_tenant_id)},
            )
            self.classroom_assessments = SqlAlchemyClassroomAssessmentRepository(
                self._connection, self._execution_tenant_id
            )
            self.idempotency = SqlAlchemyIdempotencyRepository(
                self._connection, self._execution_tenant_id
            )
            self.audit = AssessmentSecurityMutationAuditRepository(self._connection)
            self.content_authority = SqlAlchemyAssessmentContentAuthorityAdapter(
                self._connection, self._execution_tenant_id
            )
            self.teaching_composition = SqlAlchemyAssessmentTeachingCompositionAdapter(
                self._connection, self._execution_tenant_id
            )
            return self
        except Exception as exc:
            self._cleanup(suppress=True)
            reraise_as_application_error(exc)

    def commit(self) -> None:
        if self._transaction is None:
            raise PersistenceOperationFailed("Assessment Unit of Work is not active")
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


class SqlAlchemyAssessmentUnitOfWorkFactory:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def __call__(self, execution_tenant_id: UUID) -> SqlAlchemyAssessmentUnitOfWork:
        return SqlAlchemyAssessmentUnitOfWork(self._engine, execution_tenant_id)

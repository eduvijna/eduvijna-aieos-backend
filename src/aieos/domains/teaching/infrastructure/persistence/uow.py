"""SQLAlchemy Unit of Work for Teaching. Owns the transaction."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine, Transaction

from aieos.domains.teaching.application.errors import PersistenceOperationFailed
from aieos.domains.teaching.application.ports import (
    RemediationAssessmentSourceSnapshot,
)
from aieos.domains.teaching.infrastructure.persistence.assessment_source import (
    SqlAlchemyRemediationAssessmentSource,
)
from aieos.domains.teaching.infrastructure.persistence.audit_repository import (
    TeachingSecurityMutationAuditRepository,
)
from aieos.domains.teaching.infrastructure.persistence.content_eligibility import (
    SqlAlchemyContentAssignmentEligibilityAdapter,
)
from aieos.domains.teaching.infrastructure.persistence.errors import (
    reraise_as_application_error,
)
from aieos.domains.teaching.infrastructure.persistence.repositories import (
    SqlAlchemyTeachingAssignmentRepository,
    SqlAlchemyTeachingExecutionRepository,
    SqlAlchemyTeachingWorkRemediationOriginRepository,
    SqlAlchemyTeachingWorkRepository,
)
from aieos.platform.api.infrastructure.persistence.repositories import (
    SqlAlchemyIdempotencyRepository,
)
from aieos.platform.events.persistence.repositories import SqlAlchemyOutboxRepository


class SqlAlchemyTeachingUnitOfWork:
    def __init__(self, engine: Engine, execution_tenant_id: UUID) -> None:
        self._engine = engine
        self._execution_tenant_id = execution_tenant_id
        self._connection: Connection | None = None
        self._transaction: Transaction | None = None
        self.works: SqlAlchemyTeachingWorkRepository
        self.remediation_origins: SqlAlchemyTeachingWorkRemediationOriginRepository
        self.assignments: SqlAlchemyTeachingAssignmentRepository
        self.executions: SqlAlchemyTeachingExecutionRepository
        self.idempotency: SqlAlchemyIdempotencyRepository
        self.outbox: SqlAlchemyOutboxRepository
        self.audit: TeachingSecurityMutationAuditRepository
        self.content_eligibility: SqlAlchemyContentAssignmentEligibilityAdapter
        self._assessment_source: SqlAlchemyRemediationAssessmentSource

    def __enter__(self) -> SqlAlchemyTeachingUnitOfWork:
        try:
            self._connection = self._engine.connect()
            self._transaction = self._connection.begin()
            self._connection.execute(
                text("SELECT set_config('aieos.tenant_id', :tid, true)"),
                {"tid": str(self._execution_tenant_id)},
            )
            self.works = SqlAlchemyTeachingWorkRepository(
                self._connection, self._execution_tenant_id
            )
            self.remediation_origins = (
                SqlAlchemyTeachingWorkRemediationOriginRepository(
                    self._connection, self._execution_tenant_id
                )
            )
            self.assignments = SqlAlchemyTeachingAssignmentRepository(
                self._connection, self._execution_tenant_id
            )
            self.executions = SqlAlchemyTeachingExecutionRepository(
                self._connection, self._execution_tenant_id
            )
            self.idempotency = SqlAlchemyIdempotencyRepository(
                self._connection, self._execution_tenant_id
            )
            self.outbox = SqlAlchemyOutboxRepository(self._connection)
            self.audit = TeachingSecurityMutationAuditRepository(self._connection)
            self.content_eligibility = SqlAlchemyContentAssignmentEligibilityAdapter(
                self._connection, self._execution_tenant_id
            )
            self._assessment_source = SqlAlchemyRemediationAssessmentSource(
                self._connection, self._execution_tenant_id
            )
            return self
        except Exception as exc:
            self._cleanup(suppress=True)
            reraise_as_application_error(exc)

    def commit(self) -> None:
        if self._transaction is None:
            raise PersistenceOperationFailed("Teaching Unit of Work is not active")
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

    def load_recorded_assessment_for_update(
        self, assessment_id: UUID
    ) -> RemediationAssessmentSourceSnapshot | None:
        return self._assessment_source.load_for_update(assessment_id)

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


class SqlAlchemyTeachingUnitOfWorkFactory:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def __call__(self, execution_tenant_id: UUID) -> SqlAlchemyTeachingUnitOfWork:
        return SqlAlchemyTeachingUnitOfWork(self._engine, execution_tenant_id)

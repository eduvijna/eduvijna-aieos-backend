"""Teaching persistence ports. Infrastructure types are not part of this contract."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from aieos.domains.teaching.domain.identities import AggregateRevision, WorkId
from aieos.domains.teaching.domain.work import TeachingWork
from aieos.platform.idempotency.ports import IdempotencyRepository

TEACHING_WORK_CREATE = "teaching.work.create"
TEACHING_WORK_REFINE = "teaching.work.refine"


class TeachingWorkRepository(Protocol):
    """Durable persistence for the teacher-owned TeachingWork aggregate."""

    def insert(self, work: TeachingWork) -> None: ...

    def get(self, work_id: WorkId) -> TeachingWork | None: ...

    def get_for_update(self, work_id: WorkId) -> TeachingWork | None: ...

    def update(
        self,
        work: TeachingWork,
        *,
        expected_revision: AggregateRevision,
    ) -> bool: ...

    def list_for_teacher(
        self,
        *,
        teacher_principal_id: UUID,
        limit: int,
        include_archived: bool,
    ) -> list[TeachingWork]: ...

    def most_recently_updated_for_teacher(
        self,
        *,
        teacher_principal_id: UUID,
    ) -> TeachingWork | None: ...

    def count_active_for_teacher(self, *, teacher_principal_id: UUID) -> int: ...


class ReviewQueuePendingCountPort(Protocol):
    """Pending Review Queue size for the execution tenant.

    Teaching does not own the Review Queue. This port exists so the Mission
    projection can compose a count without importing Content persistence.
    """

    def pending_count(self, execution_tenant_id: UUID) -> int: ...


class TeachingClock(Protocol):
    def now(self) -> datetime: ...


class TeachingUnitOfWork(Protocol):
    works: TeachingWorkRepository
    idempotency: IdempotencyRepository

    def __enter__(self) -> TeachingUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object,
    ) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


class TeachingUnitOfWorkFactory(Protocol):
    def __call__(self, execution_tenant_id: UUID) -> TeachingUnitOfWork: ...

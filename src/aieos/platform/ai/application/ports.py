"""AI GenerationRun persistence ports."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from aieos.platform.ai.domain.generation_run import GenerationRun, GenerationRunId


class GenerationRunRepository(Protocol):
    def insert(self, run: GenerationRun) -> None: ...

    def get(self, generation_run_id: GenerationRunId) -> GenerationRun | None: ...

    def get_for_update(
        self, generation_run_id: GenerationRunId
    ) -> GenerationRun | None: ...

    def update(
        self,
        run: GenerationRun,
        *,
        expected_revision: int,
    ) -> bool: ...

    def get_by_idempotency_key(
        self,
        *,
        principal_id: UUID,
        idempotency_key_sha256: str,
    ) -> GenerationRun | None: ...

    def find_succeeded_for_work(
        self,
        *,
        principal_id: UUID,
        work_resource_id: UUID,
    ) -> GenerationRun | None: ...

    def find_active_or_succeeded_for_work(
        self,
        *,
        work_resource_id: UUID,
    ) -> GenerationRun | None: ...

    def list_for_work(
        self,
        *,
        principal_id: UUID,
        work_resource_id: UUID,
    ) -> list[GenerationRun]: ...


class AIUnitOfWork(Protocol):
    generation_runs: GenerationRunRepository

    def __enter__(self) -> AIUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object,
    ) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


class AIUnitOfWorkFactory(Protocol):
    def __call__(self, execution_tenant_id: UUID) -> AIUnitOfWork: ...

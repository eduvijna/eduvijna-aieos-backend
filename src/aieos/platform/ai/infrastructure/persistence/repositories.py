"""SQLAlchemy repositories for ai.generation_runs."""

from __future__ import annotations

from typing import Any, Mapping
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.engine import Connection

from aieos.platform.ai.application.errors import (
    GenerationRunConflict,
    PersistenceInvariantViolation,
)
from aieos.platform.ai.domain.generation_run import (
    GenerationRun,
    GenerationRunId,
    GenerationRunStatus,
)
from aieos.platform.ai.infrastructure.persistence.errors import (
    reraise_as_application_error,
)
from aieos.platform.ai.infrastructure.persistence.models import generation_runs_table


def _thaw_summary(value: Any) -> Mapping[str, object] | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        return dict(value)
    raise PersistenceInvariantViolation(
        "educational_quality_summary must be a JSON object"
    )


def _row_to_run(row: Mapping[str, Any]) -> GenerationRun:
    return GenerationRun(
        generation_run_id=GenerationRunId(row["generation_run_id"]),
        tenant_id=row["tenant_id"],
        principal_id=row["principal_id"],
        work_resource_type=row["work_resource_type"],
        work_resource_id=row["work_resource_id"],
        work_resource_revision=int(row["work_resource_revision"]),
        capability_id=row["capability_id"],
        provider_id=row["provider_id"],
        model_id=row["model_id"],
        status=GenerationRunStatus(row["status"]),
        request_fingerprint_sha256=row["request_fingerprint_sha256"],
        idempotency_key_sha256=row["idempotency_key_sha256"],
        provider_response_id=row["provider_response_id"],
        input_tokens=row["input_tokens"],
        output_tokens=row["output_tokens"],
        total_tokens=row["total_tokens"],
        educational_quality_summary=_thaw_summary(row["educational_quality_summary"]),
        result_content_id=row["result_content_id"],
        result_version_id=row["result_version_id"],
        result_content_revision=(
            None
            if row["result_content_revision"] is None
            else int(row["result_content_revision"])
        ),
        failure_code=row["failure_code"],
        aggregate_revision=int(row["aggregate_revision"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        completed_at=row["completed_at"],
        lease_expires_at=row["lease_expires_at"],
    )


def _values(run: GenerationRun) -> dict[str, Any]:
    return {
        "generation_run_id": run.generation_run_id.value,
        "tenant_id": run.tenant_id,
        "principal_id": run.principal_id,
        "work_resource_type": run.work_resource_type,
        "work_resource_id": run.work_resource_id,
        "work_resource_revision": run.work_resource_revision,
        "capability_id": run.capability_id,
        "provider_id": run.provider_id,
        "model_id": run.model_id,
        "status": run.status.value,
        "request_fingerprint_sha256": run.request_fingerprint_sha256,
        "idempotency_key_sha256": run.idempotency_key_sha256,
        "provider_response_id": run.provider_response_id,
        "input_tokens": run.input_tokens,
        "output_tokens": run.output_tokens,
        "total_tokens": run.total_tokens,
        "educational_quality_summary": (
            None
            if run.educational_quality_summary is None
            else dict(run.educational_quality_summary)
        ),
        "result_content_id": run.result_content_id,
        "result_version_id": run.result_version_id,
        "result_content_revision": run.result_content_revision,
        "failure_code": run.failure_code,
        "aggregate_revision": run.aggregate_revision,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
        "completed_at": run.completed_at,
        "lease_expires_at": run.lease_expires_at,
    }


class SqlAlchemyGenerationRunRepository:
    def __init__(self, connection: Connection, execution_tenant_id: UUID) -> None:
        self._connection = connection
        self._execution_tenant_id = execution_tenant_id

    def insert(self, run: GenerationRun) -> None:
        if run.tenant_id != self._execution_tenant_id:
            raise PersistenceInvariantViolation(
                "GenerationRun tenant_id does not match execution tenant"
            )
        try:
            self._connection.execute(generation_runs_table.insert().values(**_values(run)))
        except Exception as exc:
            reraise_as_application_error(
                exc,
                unique_conflict=GenerationRunConflict,
                unique_message="GenerationRun identity or idempotency key already exists",
            )

    def get(self, generation_run_id: GenerationRunId) -> GenerationRun | None:
        stmt = (
            select(generation_runs_table)
            .where(
                generation_runs_table.c.tenant_id == self._execution_tenant_id,
                generation_runs_table.c.generation_run_id == generation_run_id.value,
            )
            .limit(1)
        )
        try:
            row = self._connection.execute(stmt).mappings().one_or_none()
        except Exception as exc:
            reraise_as_application_error(exc)
        return None if row is None else _row_to_run(row)

    def get_for_update(
        self, generation_run_id: GenerationRunId
    ) -> GenerationRun | None:
        stmt = (
            select(generation_runs_table)
            .where(
                generation_runs_table.c.tenant_id == self._execution_tenant_id,
                generation_runs_table.c.generation_run_id == generation_run_id.value,
            )
            .with_for_update()
            .limit(1)
        )
        try:
            row = self._connection.execute(stmt).mappings().one_or_none()
        except Exception as exc:
            reraise_as_application_error(exc)
        return None if row is None else _row_to_run(row)

    def update(self, run: GenerationRun, *, expected_revision: int) -> bool:
        if run.tenant_id != self._execution_tenant_id:
            raise PersistenceInvariantViolation(
                "GenerationRun tenant_id does not match execution tenant"
            )
        values = _values(run)
        values.pop("generation_run_id")
        values.pop("tenant_id")
        values.pop("created_at")
        stmt = (
            update(generation_runs_table)
            .where(
                generation_runs_table.c.tenant_id == self._execution_tenant_id,
                generation_runs_table.c.generation_run_id == run.generation_run_id.value,
                generation_runs_table.c.aggregate_revision == expected_revision,
            )
            .values(**values)
        )
        try:
            result = self._connection.execute(stmt)
        except Exception as exc:
            reraise_as_application_error(exc)
        return result.rowcount == 1

    def get_by_idempotency_key(
        self,
        *,
        principal_id: UUID,
        idempotency_key_sha256: str,
    ) -> GenerationRun | None:
        stmt = (
            select(generation_runs_table)
            .where(
                generation_runs_table.c.tenant_id == self._execution_tenant_id,
                generation_runs_table.c.principal_id == principal_id,
                generation_runs_table.c.idempotency_key_sha256 == idempotency_key_sha256,
            )
            .limit(1)
        )
        try:
            row = self._connection.execute(stmt).mappings().one_or_none()
        except Exception as exc:
            reraise_as_application_error(exc)
        return None if row is None else _row_to_run(row)

    def find_succeeded_for_work(
        self,
        *,
        principal_id: UUID,
        work_resource_id: UUID,
    ) -> GenerationRun | None:
        stmt = (
            select(generation_runs_table)
            .where(
                generation_runs_table.c.tenant_id == self._execution_tenant_id,
                generation_runs_table.c.principal_id == principal_id,
                generation_runs_table.c.work_resource_id == work_resource_id,
                generation_runs_table.c.status == GenerationRunStatus.SUCCEEDED.value,
            )
            .order_by(generation_runs_table.c.created_at.asc())
            .limit(1)
        )
        try:
            row = self._connection.execute(stmt).mappings().one_or_none()
        except Exception as exc:
            reraise_as_application_error(exc)
        return None if row is None else _row_to_run(row)

    def find_outcome_for_work_revision_capability(
        self,
        *,
        work_resource_id: UUID,
        work_resource_revision: int,
        capability_id: str,
    ) -> GenerationRun | None:
        """Fence A holder: RUNNING or SUCCEEDED for work+revision+capability."""
        stmt = (
            select(generation_runs_table)
            .where(
                generation_runs_table.c.tenant_id == self._execution_tenant_id,
                generation_runs_table.c.work_resource_id == work_resource_id,
                generation_runs_table.c.work_resource_revision
                == work_resource_revision,
                generation_runs_table.c.capability_id == capability_id,
                generation_runs_table.c.status.in_(
                    (
                        GenerationRunStatus.RUNNING.value,
                        GenerationRunStatus.SUCCEEDED.value,
                    )
                ),
            )
            .order_by(generation_runs_table.c.created_at.asc())
            .limit(1)
        )
        try:
            row = self._connection.execute(stmt).mappings().one_or_none()
        except Exception as exc:
            reraise_as_application_error(exc)
        return None if row is None else _row_to_run(row)

    def find_running_for_work_capability(
        self,
        *,
        work_resource_id: UUID,
        capability_id: str,
    ) -> GenerationRun | None:
        """Fence B holder: RUNNING for work+capability across all revisions."""
        stmt = (
            select(generation_runs_table)
            .where(
                generation_runs_table.c.tenant_id == self._execution_tenant_id,
                generation_runs_table.c.work_resource_id == work_resource_id,
                generation_runs_table.c.capability_id == capability_id,
                generation_runs_table.c.status == GenerationRunStatus.RUNNING.value,
            )
            .order_by(generation_runs_table.c.created_at.asc())
            .limit(1)
        )
        try:
            row = self._connection.execute(stmt).mappings().one_or_none()
        except Exception as exc:
            reraise_as_application_error(exc)
        return None if row is None else _row_to_run(row)

    def list_for_work(
        self,
        *,
        principal_id: UUID,
        work_resource_id: UUID,
    ) -> list[GenerationRun]:
        stmt = (
            select(generation_runs_table)
            .where(
                generation_runs_table.c.tenant_id == self._execution_tenant_id,
                generation_runs_table.c.principal_id == principal_id,
                generation_runs_table.c.work_resource_id == work_resource_id,
            )
            .order_by(generation_runs_table.c.created_at.asc())
        )
        try:
            rows = self._connection.execute(stmt).mappings().all()
        except Exception as exc:
            reraise_as_application_error(exc)
        return [_row_to_run(row) for row in rows]

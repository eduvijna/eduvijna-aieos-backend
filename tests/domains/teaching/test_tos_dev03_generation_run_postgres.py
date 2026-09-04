"""TOS-DEV03 GenerationRun PostgreSQL migration / RLS / status tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from dataclasses import replace

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.platform.ai.application.errors import GenerationRunConflict
from aieos.platform.ai.domain.generation_run import (
    GenerationRun,
    GenerationRunId,
    GenerationRunStatus,
)
from aieos.platform.ai.infrastructure.persistence.uow import (
    SqlAlchemyAIUnitOfWorkFactory,
)
from aieos.platform.idempotency.hashing import fingerprint_material, hash_idempotency_key
from tests.dbutil import set_tenant

pytestmark = pytest.mark.tos_dev03

FIXED_NOW = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)


def _run(
    *,
    tenant_id: uuid.UUID,
    principal_id: uuid.UUID,
    work_id: uuid.UUID | None = None,
    status: GenerationRunStatus = GenerationRunStatus.RUNNING,
    key: str = "key-1",
    fingerprint: str | None = None,
    revision: int = 0,
    lease_expires_at: datetime | None = None,
) -> GenerationRun:
    if status is GenerationRunStatus.RUNNING and lease_expires_at is None:
        lease_expires_at = FIXED_NOW + timedelta(seconds=120)
    return GenerationRun(
        generation_run_id=GenerationRunId.generate(),
        tenant_id=tenant_id,
        principal_id=principal_id,
        work_resource_type="teaching.work",
        work_resource_id=work_id or uuid.uuid7(),
        work_resource_revision=0,
        capability_id="education.generate_worksheet",
        provider_id="fake",
        model_id="fake-model",
        status=status,
        request_fingerprint_sha256=fingerprint
        or fingerprint_material({"work_id": "x", "rev": 0}),
        idempotency_key_sha256=hash_idempotency_key(key),
        provider_response_id=None,
        input_tokens=None,
        output_tokens=None,
        total_tokens=None,
        educational_quality_summary=None,
        result_content_id=None,
        result_version_id=None,
        result_content_revision=None,
        failure_code=None,
        aggregate_revision=revision,
        created_at=FIXED_NOW,
        updated_at=FIXED_NOW,
        completed_at=None,
        lease_expires_at=lease_expires_at,
    )


class TestGenerationRunPostgres:
    def test_migration_creates_ai_schema_and_table(
        self, bootstrap_engine: Engine
    ) -> None:
        with bootstrap_engine.connect() as conn:
            exists = conn.execute(
                text(
                    """
                    SELECT to_regclass('ai.generation_runs') IS NOT NULL
                    """
                )
            ).scalar_one()
            assert exists is True
            head = conn.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            assert head == "tosd090002"
            lease_col = conn.execute(
                text(
                    """
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_schema = 'ai'
                      AND table_name = 'generation_runs'
                      AND column_name = 'lease_expires_at'
                    """
                )
            ).scalar_one_or_none()
            assert lease_col == 1

    def test_rls_isolates_tenants(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_a = uuid.uuid7()
        tenant_b = uuid.uuid7()
        principal = uuid.uuid7()
        factory = SqlAlchemyAIUnitOfWorkFactory(runtime_engine)
        run_a = _run(tenant_id=tenant_a, principal_id=principal, key="a")
        with factory(tenant_a) as uow:
            uow.generation_runs.insert(run_a)
            uow.commit()
        with factory(tenant_b) as uow:
            assert uow.generation_runs.get(run_a.generation_run_id) is None
        with bootstrap_engine.connect() as conn:
            set_tenant(conn, tenant_a)
            count = conn.execute(
                text(
                    "SELECT count(*) FROM ai.generation_runs WHERE tenant_id = :tid"
                ),
                {"tid": tenant_a},
            ).scalar_one()
            assert count == 1

    def test_idempotency_unique_and_status_transitions(
        self, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        factory = SqlAlchemyAIUnitOfWorkFactory(runtime_engine)
        run = _run(tenant_id=tenant_id, principal_id=principal_id, key="same")
        with factory(tenant_id) as uow:
            uow.generation_runs.insert(run)
            uow.commit()
        duplicate = _run(
            tenant_id=tenant_id,
            principal_id=principal_id,
            key="same",
            fingerprint=run.request_fingerprint_sha256,
        )
        with factory(tenant_id) as uow:
            with pytest.raises(Exception):
                uow.generation_runs.insert(duplicate)
                uow.commit()

        # Ordinary path: RUNNING → SUCCEEDED (no durable VALIDATED).
        with factory(tenant_id) as uow:
            locked = uow.generation_runs.get_for_update(run.generation_run_id)
            assert locked is not None
            succeeded = replace(
                locked,
                status=GenerationRunStatus.SUCCEEDED,
                aggregate_revision=1,
                result_content_id=uuid.uuid7(),
                result_version_id=uuid.uuid7(),
                result_content_revision=2,
                lease_expires_at=None,
                updated_at=FIXED_NOW,
                completed_at=FIXED_NOW,
            )
            assert uow.generation_runs.update(succeeded, expected_revision=0)
            uow.commit()

        with factory(tenant_id) as uow:
            found = uow.generation_runs.get(run.generation_run_id)
            assert found is not None
            assert found.status is GenerationRunStatus.SUCCEEDED
            assert found.aggregate_revision == 1

    def test_work_fence_blocks_second_running_insert(
        self, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        work_id = uuid.uuid7()
        factory = SqlAlchemyAIUnitOfWorkFactory(runtime_engine)
        first = _run(
            tenant_id=tenant_id,
            principal_id=principal_id,
            work_id=work_id,
            key="fence-a",
        )
        with factory(tenant_id) as uow:
            uow.generation_runs.insert(first)
            uow.commit()
        second = _run(
            tenant_id=tenant_id,
            principal_id=principal_id,
            work_id=work_id,
            key="fence-b",
        )
        with factory(tenant_id) as uow:
            with pytest.raises(GenerationRunConflict):
                uow.generation_runs.insert(second)
                uow.commit()

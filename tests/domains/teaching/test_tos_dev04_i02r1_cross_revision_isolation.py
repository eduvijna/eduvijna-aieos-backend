"""TOS-DEV04-I02R1 cross-revision stale-run result isolation proofs."""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy.engine import Engine

from aieos.platform.ai.domain.generation_run import (
    GenerationRun,
    GenerationRunId,
    GenerationRunStatus,
)
from aieos.platform.ai.fake import FakeStructuredModelGateway
from aieos.platform.ai.infrastructure.persistence.uow import (
    SqlAlchemyAIUnitOfWorkFactory,
)
from aieos.platform.idempotency.hashing import fingerprint_material, hash_idempotency_key
from aieos.domains.content.infrastructure.persistence.uow import (
    SqlAlchemyContentUnitOfWorkFactory,
)
from tests.domains.teaching.helpers_dev03 import build_client, create_work, headers
from tests.domains.teaching.worksheet_fixtures import valid_worksheet_model

pytestmark = [
    pytest.mark.tos_dev03,
    pytest.mark.tos_dev03r1,
    pytest.mark.tos_dev04_i02,
]


@pytest.fixture(autouse=True)
def _cleanup_i02r1_shared_db_rows(postgres18: dict[str, str]) -> None:
    """Remove multi-outcome runs that would block other suites' Alembic downgrades."""
    from sqlalchemy import create_engine

    from tests.domains.teaching.test_tos_dev04_i02_multi_artifact_persistence import (
        _clear_i02_downgrade_blockers,
    )

    yield
    engine = create_engine(postgres18["bootstrap_url"])
    try:
        _clear_i02_downgrade_blockers(engine)
    finally:
        engine.dispose()


def _etag(response) -> str:
    return response.headers["ETag"]


def _create_and_etag(client, tenant_id: uuid.UUID, *, key: str) -> tuple[str, str]:
    created = create_work(
        client,
        tenant_id,
        goal_text=(
            "Tomorrow my Grade 5 students need to understand fractions "
            "using visual examples."
        ),
        target_date="2026-08-29",
        idempotency_key=key,
    )
    assert created.status_code == 201, created.text
    return created.json()["work_id"], _etag(created)


def _crash_run_to_stale_running(
    runtime_engine: Engine,
    *,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    clear_result_fields: bool = True,
) -> None:
    factory = SqlAlchemyAIUnitOfWorkFactory(runtime_engine)
    with factory(tenant_id) as uow:
        locked = uow.generation_runs.get_for_update(GenerationRunId(run_id))
        assert locked is not None
        stale_at = locked.updated_at
        crashed = replace(
            locked,
            status=GenerationRunStatus.RUNNING,
            result_content_id=None if clear_result_fields else locked.result_content_id,
            result_version_id=None if clear_result_fields else locked.result_version_id,
            result_content_revision=(
                None if clear_result_fields else locked.result_content_revision
            ),
            completed_at=None,
            lease_expires_at=stale_at - timedelta(seconds=30),
            updated_at=stale_at,
            aggregate_revision=locked.aggregate_revision + 1,
        )
        assert uow.generation_runs.update(
            crashed, expected_revision=locked.aggregate_revision
        )
        uow.commit()


def _refine(client, tenant_id: uuid.UUID, work_id: str, etag: str, *, key: str) -> str:
    refined = client.patch(
        f"/api/v1/teaching/works/{work_id}",
        json={"goal_text": "Refined goal for revision R1 with clearer fraction focus."},
        headers=headers(tenant_id, idempotency_key=key, if_match=etag),
    )
    assert refined.status_code == 200, refined.text
    assert refined.json()["aggregate_revision"] == 1
    return _etag(refined)


class TestCrossRevisionStaleIsolation:
    def test_stale_r0_with_committed_content_does_not_satisfy_r1(
        self, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        gateway = FakeStructuredModelGateway(
            result_factory=lambda _r: valid_worksheet_model()
        )
        client = build_client(
            runtime_engine,
            tenant_id,
            principal_id,
            model_gateway=gateway,
            generation_lease_seconds=120,
        )
        work_id, etag0 = _create_and_etag(client, tenant_id, key="i02r1-create-content")
        generated_r0 = client.post(
            f"/api/v1/teaching/works/{work_id}/actions/generate",
            headers=headers(
                tenant_id, idempotency_key="i02r1-gen-r0-content", if_match=etag0
            ),
        )
        assert generated_r0.status_code == 200, generated_r0.text
        r0_body = generated_r0.json()
        r0_run_id = uuid.UUID(r0_body["generation_run_id"])
        r0_content_id = r0_body["artifact"]["content_id"]
        r0_version_id = r0_body["artifact"]["version_id"]
        assert gateway.call_count == 1

        _crash_run_to_stale_running(
            runtime_engine, tenant_id=tenant_id, run_id=r0_run_id
        )
        etag1 = _refine(
            client, tenant_id, work_id, etag0, key="i02r1-refine-content"
        )

        generated_r1 = client.post(
            f"/api/v1/teaching/works/{work_id}/actions/generate",
            headers=headers(
                tenant_id, idempotency_key="i02r1-gen-r1-content", if_match=etag1
            ),
        )
        assert generated_r1.status_code == 200, generated_r1.text
        r1_body = generated_r1.json()
        r1_run_id = uuid.UUID(r1_body["generation_run_id"])
        assert r1_run_id != r0_run_id
        assert r1_body["artifact"]["content_id"] != r0_content_id
        assert r1_body["artifact"]["version_id"] != r0_version_id
        assert gateway.call_count == 2

        factory = SqlAlchemyAIUnitOfWorkFactory(runtime_engine)
        with factory(tenant_id) as uow:
            r0 = uow.generation_runs.get(GenerationRunId(r0_run_id))
            r1 = uow.generation_runs.get(GenerationRunId(r1_run_id))
            assert r0 is not None
            assert r1 is not None
            assert r0.status is GenerationRunStatus.SUCCEEDED
            assert r0.work_resource_revision == 0
            assert r0.result_content_id == uuid.UUID(r0_content_id)
            assert r0.result_version_id == uuid.UUID(r0_version_id)
            assert r1.status is GenerationRunStatus.SUCCEEDED
            assert r1.work_resource_revision == 1
            assert r1.result_content_id == uuid.UUID(r1_body["artifact"]["content_id"])

        content_factory = SqlAlchemyContentUnitOfWorkFactory(runtime_engine)
        with content_factory(tenant_id) as uow:
            from aieos.domains.content.domain.identities import ContentVersionId
            from aieos.domains.content.domain.provenance import (
                AIGenerationProvenanceV1,
                ai_generation_provenance_from_json,
            )

            prov_raw = uow.versions.get_provenance(ContentVersionId(uuid.UUID(r0_version_id)))
            assert prov_raw is not None
            parsed = ai_generation_provenance_from_json(prov_raw)
            assert isinstance(parsed, AIGenerationProvenanceV1)
            assert any(
                ref.resource_type == "teaching.work"
                and ref.resource_id == uuid.UUID(work_id)
                and ref.resource_revision == 0
                for ref in parsed.source_refs
            )

    def test_stale_r0_zero_content_releases_fence_b_before_r1_claim(
        self, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        gateway = FakeStructuredModelGateway(
            result_factory=lambda _r: valid_worksheet_model()
        )
        client = build_client(
            runtime_engine,
            tenant_id,
            principal_id,
            model_gateway=gateway,
            generation_lease_seconds=120,
        )
        work_id, etag0 = _create_and_etag(client, tenant_id, key="i02r1-create-zero")
        work_uuid = uuid.UUID(work_id)

        # Seed R0 RUNNING stale with zero Content (Fence B holder).
        from datetime import UTC, datetime

        fixed_now = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)
        r0_run = GenerationRun(
            generation_run_id=GenerationRunId.generate(),
            tenant_id=tenant_id,
            principal_id=principal_id,
            work_resource_type="teaching.work",
            work_resource_id=work_uuid,
            work_resource_revision=0,
            capability_id="education.generate_worksheet",
            provider_id="fake",
            model_id="fake-model",
            status=GenerationRunStatus.RUNNING,
            request_fingerprint_sha256=fingerprint_material(
                {
                    "work_id": str(work_uuid),
                    "work_revision": 0,
                    "capability_id": "education.generate_worksheet",
                    "provider_id": "fake",
                    "model_id": "fake-model",
                }
            ),
            idempotency_key_sha256=hash_idempotency_key("i02r1-seed-r0-zero"),
            provider_response_id=None,
            input_tokens=None,
            output_tokens=None,
            total_tokens=None,
            educational_quality_summary=None,
            result_content_id=None,
            result_version_id=None,
            result_content_revision=None,
            failure_code=None,
            aggregate_revision=0,
            created_at=fixed_now,
            updated_at=fixed_now,
            completed_at=None,
            lease_expires_at=fixed_now - timedelta(seconds=30),
        )
        factory = SqlAlchemyAIUnitOfWorkFactory(runtime_engine)
        with factory(tenant_id) as uow:
            uow.generation_runs.insert(r0_run)
            uow.commit()

        etag1 = _refine(client, tenant_id, work_id, etag0, key="i02r1-refine-zero")

        # Fresh lease would block; stale zero-content must release then allow R1.
        generated_r1 = client.post(
            f"/api/v1/teaching/works/{work_id}/actions/generate",
            headers=headers(
                tenant_id, idempotency_key="i02r1-gen-r1-zero", if_match=etag1
            ),
        )
        assert generated_r1.status_code == 200, generated_r1.text
        r1_body = generated_r1.json()
        r1_run_id = uuid.UUID(r1_body["generation_run_id"])
        assert r1_run_id != r0_run.generation_run_id.value
        assert gateway.call_count == 1

        with factory(tenant_id) as uow:
            r0 = uow.generation_runs.get(r0_run.generation_run_id)
            r1 = uow.generation_runs.get(GenerationRunId(r1_run_id))
            assert r0 is not None
            assert r1 is not None
            assert r0.status is GenerationRunStatus.FAILED
            assert r0.failure_code == "generation_lease_expired"
            assert r0.work_resource_revision == 0
            assert r1.status is GenerationRunStatus.SUCCEEDED
            assert r1.work_resource_revision == 1
            running = uow.generation_runs.find_running_for_work_capability(
                work_resource_id=work_uuid,
                capability_id="education.generate_worksheet",
            )
            assert running is None

    def test_fresh_cross_revision_running_stays_in_progress(
        self, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        gateway = FakeStructuredModelGateway(
            result_factory=lambda _r: valid_worksheet_model()
        )
        client = build_client(
            runtime_engine,
            tenant_id,
            principal_id,
            model_gateway=gateway,
            generation_lease_seconds=600,
        )
        work_id, etag0 = _create_and_etag(client, tenant_id, key="i02r1-create-fresh")
        work_uuid = uuid.UUID(work_id)
        from datetime import UTC, datetime

        now = datetime.now(tz=UTC)
        r0_run = GenerationRun(
            generation_run_id=GenerationRunId.generate(),
            tenant_id=tenant_id,
            principal_id=principal_id,
            work_resource_type="teaching.work",
            work_resource_id=work_uuid,
            work_resource_revision=0,
            capability_id="education.generate_worksheet",
            provider_id="fake",
            model_id="fake-model",
            status=GenerationRunStatus.RUNNING,
            request_fingerprint_sha256=fingerprint_material({"marker": "fresh-r0"}),
            idempotency_key_sha256=hash_idempotency_key("i02r1-seed-r0-fresh"),
            provider_response_id=None,
            input_tokens=None,
            output_tokens=None,
            total_tokens=None,
            educational_quality_summary=None,
            result_content_id=None,
            result_version_id=None,
            result_content_revision=None,
            failure_code=None,
            aggregate_revision=0,
            created_at=now,
            updated_at=now,
            completed_at=None,
            lease_expires_at=now + timedelta(seconds=600),
        )
        factory = SqlAlchemyAIUnitOfWorkFactory(runtime_engine)
        with factory(tenant_id) as uow:
            uow.generation_runs.insert(r0_run)
            uow.commit()

        etag1 = _refine(client, tenant_id, work_id, etag0, key="i02r1-refine-fresh")
        blocked = client.post(
            f"/api/v1/teaching/works/{work_id}/actions/generate",
            headers=headers(
                tenant_id, idempotency_key="i02r1-gen-r1-fresh", if_match=etag1
            ),
        )
        assert blocked.status_code == 409, blocked.text
        assert blocked.json()["code"] == "work_generation_in_progress"
        assert gateway.call_count == 0

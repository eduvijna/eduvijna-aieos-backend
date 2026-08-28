"""TOS-DEV03R3 Teaching durable failure codes + same-key replay (offline)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.engine import Engine

from aieos.platform.ai.domain.generation_run import GenerationRunStatus
from aieos.platform.ai.fake import FakeStructuredModelGateway
from aieos.platform.ai.gateway import (
    ModelAdapterContractFailed,
    ModelGenerationFailed,
    ModelOutputInvalid,
    ModelProviderUnavailable,
    ModelRequestRejected,
)
from aieos.platform.ai.infrastructure.persistence.uow import (
    SqlAlchemyAIUnitOfWorkFactory,
)
from tests.domains.teaching.helpers_dev03 import build_client, create_work, headers
from tests.domains.teaching.worksheet_fixtures import valid_worksheet_model

pytestmark = [pytest.mark.tos_dev03, pytest.mark.tos_dev03r3]


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
        target_date="2026-08-28",
        idempotency_key=key,
    )
    assert created.status_code == 201, created.text
    return created.json()["work_id"], _etag(created)


@pytest.mark.parametrize(
    ("gateway_error", "durable_code", "public_code", "http_status"),
    [
        (ModelRequestRejected("rejected"), "model_request_rejected", "model_generation_failed", 502),
        (
            ModelAdapterContractFailed("contract"),
            "model_adapter_contract_failed",
            "model_generation_failed",
            502,
        ),
        (ModelOutputInvalid("invalid"), "model_output_invalid", "model_output_invalid", 502),
        (
            ModelProviderUnavailable("down"),
            "model_provider_unavailable",
            "model_provider_unavailable",
            503,
        ),
        (ModelGenerationFailed("failed"), "model_generation_failed", "model_generation_failed", 502),
    ],
)
def test_failure_taxonomy_durable_and_same_key_replay(
    runtime_engine: Engine,
    gateway_error: Exception,
    durable_code: str,
    public_code: str,
    http_status: int,
) -> None:
    tenant_id = uuid.uuid7()
    principal_id = uuid.uuid7()
    gateway = FakeStructuredModelGateway(error=gateway_error)
    client = build_client(
        runtime_engine, tenant_id, principal_id, model_gateway=gateway
    )
    work_id, etag = _create_and_etag(
        client, tenant_id, key=f"r3-create-{durable_code}"
    )
    failed = client.post(
        f"/api/v1/teaching/works/{work_id}/actions/generate",
        headers=headers(
            tenant_id, idempotency_key=f"r3-gen-{durable_code}", if_match=etag
        ),
    )
    assert failed.status_code == http_status, failed.text
    assert failed.json()["code"] == public_code
    assert gateway.call_count == 1

    factory = SqlAlchemyAIUnitOfWorkFactory(runtime_engine)
    with factory(tenant_id) as uow:
        runs = uow.generation_runs.list_for_work(
            principal_id=principal_id,
            work_resource_id=uuid.UUID(work_id),
        )
        assert len(runs) == 1
        assert runs[0].status is GenerationRunStatus.FAILED
        assert runs[0].failure_code == durable_code
        assert runs[0].lease_expires_at is None
        assert runs[0].result_content_id is None
        assert runs[0].result_version_id is None

    replay = client.post(
        f"/api/v1/teaching/works/{work_id}/actions/generate",
        headers=headers(
            tenant_id, idempotency_key=f"r3-gen-{durable_code}", if_match=etag
        ),
    )
    assert replay.status_code == http_status, replay.text
    assert replay.json()["code"] == public_code
    assert gateway.call_count == 1

    contents = client.get("/api/v1/contents", headers=headers(tenant_id))
    assert contents.status_code == 200
    assert contents.json()["items"] == []
    queue = client.get("/api/v1/teacher-os/review-queue", headers=headers(tenant_id))
    assert queue.status_code == 200
    assert queue.json()["items"] == []

"""TOS-DEV03R4 Teaching durable failure codes for output completion taxonomy."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.engine import Engine

from aieos.platform.ai.domain.generation_run import GenerationRunStatus
from aieos.platform.ai.fake import FakeStructuredModelGateway
from aieos.platform.ai.gateway import ModelOutputIncomplete, ModelOutputMissing
from aieos.platform.ai.infrastructure.persistence.uow import SqlAlchemyAIUnitOfWorkFactory
from tests.domains.teaching.helpers_dev03 import build_client, create_work, headers

pytestmark = [pytest.mark.tos_dev03, pytest.mark.tos_dev03r4]


def _etag(response) -> str:
    return response.headers["ETag"]


def _create_and_etag(client, tenant_id: uuid.UUID, *, key: str) -> tuple[str, str]:
    created = create_work(
        client,
        tenant_id,
        goal_text="Grade 5 fractions with visual examples.",
        target_date="2026-08-28",
        idempotency_key=key,
    )
    assert created.status_code == 201, created.text
    return created.json()["work_id"], _etag(created)


@pytest.mark.parametrize(
    ("gateway_error", "durable_code"),
    [
        (ModelOutputIncomplete("incomplete"), "model_output_incomplete"),
        (ModelOutputMissing("missing"), "model_output_missing"),
    ],
)
def test_output_completion_failure_replay(
    runtime_engine: Engine,
    gateway_error: Exception,
    durable_code: str,
) -> None:
    tenant_id = uuid.uuid7()
    principal_id = uuid.uuid7()
    gateway = FakeStructuredModelGateway(error=gateway_error)
    client = build_client(
        runtime_engine, tenant_id, principal_id, model_gateway=gateway
    )
    work_id, etag = _create_and_etag(
        client, tenant_id, key=f"r4-create-{durable_code}"
    )
    failed = client.post(
        f"/api/v1/teaching/works/{work_id}/actions/generate",
        headers=headers(
            tenant_id, idempotency_key=f"r4-gen-{durable_code}", if_match=etag
        ),
    )
    assert failed.status_code == 502, failed.text
    assert failed.json()["code"] == "model_output_invalid"
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

    replay = client.post(
        f"/api/v1/teaching/works/{work_id}/actions/generate",
        headers=headers(
            tenant_id, idempotency_key=f"r4-gen-{durable_code}", if_match=etag
        ),
    )
    assert replay.status_code == 502, replay.text
    assert replay.json()["code"] == "model_output_invalid"
    assert gateway.call_count == 1

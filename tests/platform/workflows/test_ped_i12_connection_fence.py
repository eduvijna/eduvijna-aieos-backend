"""PED-I12 Temporal connect + operation-fence tests (no Temporal Cloud)."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from aieos.platform.runtime.config_workflow_dispatcher import (
    WorkflowDispatcherRuntimeConfig,
)
from aieos.platform.runtime.models import DeploymentEnvironment, ReleaseIdentity
from aieos.platform.workflows.constants import (
    CONTENT_REVIEW_TASK_QUEUE,
    CONTENT_REVIEW_WORKFLOW_TYPE,
    ERROR_TASK_QUEUE_MISMATCH,
    SIGNAL_REVIEW_DECISION_RECORDED,
)
from aieos.platform.workflows.temporal.connection import (
    connect_workflow_dispatcher_temporal,
    workflow_dispatcher_client_identity,
)
from aieos.platform.workflows.temporal.content_review import ContentReviewWorkflowV1
from aieos.platform.workflows.temporal.gateway import TemporalClientReviewGateway

pytestmark = pytest.mark.ped_i12

_DISPATCHER_API_KEY = "dispatcher-api-key-SECRET-MATERIAL"
_WORKER_API_KEY = "worker-api-key-MUST-NOT-BE-USED"


def _cfg(*, connect_timeout: int = 1) -> WorkflowDispatcherRuntimeConfig:
    return WorkflowDispatcherRuntimeConfig(
        environment=DeploymentEnvironment.PRODUCTION,
        release_identity=ReleaseIdentity(
            application_version="0.1.0",
            git_sha="a" * 40,
            build_id="build-42",
            artifact_digest="sha256:" + ("c" * 64),
        ),
        database_url="postgresql+psycopg://u:p@localhost/db",
        database_role="aieos_workflow_dispatcher",
        database_connect_timeout_seconds=5,
        temporal_target_host="dispatcher.temporal.example:7233",
        temporal_namespace="aieos-dispatcher-ns",
        temporal_api_key=_DISPATCHER_API_KEY,
        temporal_connect_timeout_seconds=connect_timeout,
        poll_interval_seconds=2,
        candidate_batch_size=10,
        max_intents_per_tenant_per_pass=1,
        claim_lease_seconds=30,
        max_attempts=3,
        retry_delay_seconds=1,
        result_timeout_seconds=30,
        start_reconciliation_timeout_seconds=10,
        shutdown_grace_seconds=5,
    )


def test_client_identity_shape() -> None:
    assert workflow_dispatcher_client_identity(_cfg()) == (
        "aieos.workflow-dispatcher.content-review/build-42"
    )


def test_connect_uses_tls_dispatcher_key_namespace_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def _fake_connect(target_host, **kwargs):
        captured["target_host"] = target_host
        captured.update(kwargs)
        return MagicMock(name="client")

    monkeypatch.setattr(
        "aieos.platform.workflows.temporal.connection.Client.connect",
        _fake_connect,
    )

    async def _run():
        return await connect_workflow_dispatcher_temporal(_cfg(connect_timeout=5))

    client = asyncio.run(_run())
    assert client is not None
    assert captured["target_host"] == "dispatcher.temporal.example:7233"
    assert captured["namespace"] == "aieos-dispatcher-ns"
    assert captured["api_key"] == _DISPATCHER_API_KEY
    assert captured["api_key"] != _WORKER_API_KEY
    assert captured["tls"] is True
    assert captured["identity"] == (
        "aieos.workflow-dispatcher.content-review/build-42"
    )


def test_outer_connect_timeout_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _hang(*args, **kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(
        "aieos.platform.workflows.temporal.connection.Client.connect",
        _hang,
    )

    async def _run():
        with pytest.raises(TimeoutError) as ei:
            await connect_workflow_dispatcher_temporal(_cfg(connect_timeout=1))
        err = str(ei.value)
        assert _DISPATCHER_API_KEY not in err
        assert "SECRET" not in err

    asyncio.run(_run())


def test_exact_task_queue_succeeds_without_calling_wrong_queue() -> None:
    client = MagicMock()
    client.start_workflow = AsyncMock(return_value=MagicMock())
    gateway = TemporalClientReviewGateway(client)

    async def _run():
        result = await gateway.start_content_review(
            temporal_workflow_id="wf-1",
            task_queue=CONTENT_REVIEW_TASK_QUEUE,
            start_input={
                "workflow_instance_id": "i1",
                "content_id": "c1",
                "version_id": "v1",
            },
            reconciliation_timeout_seconds=5.0,
        )
        assert result.delivered is True
        kwargs = client.start_workflow.await_args.kwargs
        assert kwargs["task_queue"] == CONTENT_REVIEW_TASK_QUEUE
        assert client.start_workflow.await_args.args[0] is ContentReviewWorkflowV1.run

    asyncio.run(_run())


@pytest.mark.parametrize("bad_queue", ["", "  ", "other.queue", "aieos.content.other"])
def test_arbitrary_or_blank_task_queue_fails_before_temporal(bad_queue: str) -> None:
    client = MagicMock()
    client.start_workflow = AsyncMock()
    gateway = TemporalClientReviewGateway(client)

    async def _run():
        result = await gateway.start_content_review(
            temporal_workflow_id="wf-1",
            task_queue=bad_queue,
            start_input={
                "workflow_instance_id": "i1",
                "content_id": "c1",
                "version_id": "v1",
            },
            reconciliation_timeout_seconds=5.0,
        )
        assert result.delivered is False
        assert result.permanent is True
        assert result.error_code == ERROR_TASK_QUEUE_MISMATCH
        client.start_workflow.assert_not_awaited()

    asyncio.run(_run())


def test_hardcoded_workflow_and_signal_constants() -> None:
    assert CONTENT_REVIEW_WORKFLOW_TYPE == "ContentReviewWorkflowV1"
    assert CONTENT_REVIEW_TASK_QUEUE == "aieos.content.review"
    assert SIGNAL_REVIEW_DECISION_RECORDED == "review_decision_recorded"
    assert ContentReviewWorkflowV1.__name__ == "ContentReviewWorkflowV1"


def test_gateway_has_no_admin_methods() -> None:
    forbidden = (
        "terminate",
        "cancel",
        "reset",
        "create_schedule",
        "update_schedule",
        "delete_schedule",
        "list_schedules",
    )
    for name in forbidden:
        assert not hasattr(TemporalClientReviewGateway, name)

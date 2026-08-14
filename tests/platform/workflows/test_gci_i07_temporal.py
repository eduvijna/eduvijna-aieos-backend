"""GCI-I07 Temporal start/command delivery, replay, cancellation, security."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer

from aieos.platform.workflows.constants import (
    CONTENT_REVIEW_TASK_QUEUE,
    ERROR_RETRY_EXHAUSTED,
    ERROR_TEMPORAL_UNAVAILABLE,
    INTENT_DELIVERED,
    INTENT_PENDING,
    INTENT_QUARANTINED,
    PROCESS_DECISION_OBSERVED,
    PROCESS_WAITING,
)
from aieos.platform.workflows.persistence.repositories import (
    SqlAlchemyWorkflowDispatcherRepository,
)
from aieos.platform.workflows.temporal.content_review import ContentReviewWorkflowV1
from aieos.platform.workflows.temporal.dispatchers import DispatcherConfig
from aieos.platform.workflows.temporal.gateway import (
    CommandDeliveryResult,
    StartDeliveryResult,
    TemporalClientReviewGateway,
)
from aieos.platform.workflows.temporal.worker import create_content_review_worker
from tests.fakes import AllowReviewAuthorization
from tests.platform.workflows.helpers import (
    client_for,
    command_dispatcher,
    command_intent_rows,
    content_row,
    decide,
    decision_count,
    default_dispatcher_config,
    generated_version,
    in_review,
    run_async,
    start_dispatcher,
    start_intent_rows,
    submit_review,
)

pytestmark = pytest.mark.gci_i07

HISTORY_FORBIDDEN = (
    "SENSITIVE_TEST_COMMENT",
    "reason_code_should_not_leak",
    "Title",
    "Description",
    "Idempotency-Key",
    '"marker"',
)


class UnavailableGateway:
    async def start_content_review(self, **kwargs) -> StartDeliveryResult:
        return StartDeliveryResult(
            delivered=False, error_code=ERROR_TEMPORAL_UNAVAILABLE, permanent=False
        )

    async def deliver_review_decision(self, **kwargs) -> CommandDeliveryResult:
        return CommandDeliveryResult(
            delivered=False, error_code=ERROR_TEMPORAL_UNAVAILABLE, permanent=False
        )


class IdentityConflictGateway:
    async def start_content_review(self, **kwargs) -> StartDeliveryResult:
        return StartDeliveryResult(
            delivered=False,
            error_code="workflow_identity_conflict",
            permanent=True,
        )

    async def deliver_review_decision(self, **kwargs) -> CommandDeliveryResult:
        return CommandDeliveryResult(
            delivered=False,
            error_code="workflow_terminal_mismatch",
            permanent=True,
        )


def _assert_history_minimized(history_json: str) -> None:
    lowered = history_json.lower()
    for needle in HISTORY_FORBIDDEN:
        assert needle.lower() not in lowered


class TestTemporalStartAndCommand:
    def test_start_query_approve_and_replay(
        self, runtime_engine, workflow_dispatcher_engine, bootstrap_engine, postgres18
    ) -> None:
        assert postgres18["server_version"].startswith("18.")

        async def scenario() -> None:
            async with await WorkflowEnvironment.start_time_skipping() as env:
                tenant_id = uuid.uuid7()
                principal_id = uuid.uuid7()
                client = client_for(runtime_engine, tenant_id, principal_id)
                content_id, version_id, etag = generated_version(client, tenant_id)
                submitted = submit_review(
                    client, tenant_id, content_id, version_id, etag=etag
                )
                assert submitted.status_code == 200
                gateway = TemporalClientReviewGateway(env.client)
                # Start accepted without worker first.
                started = await start_dispatcher(
                    workflow_dispatcher_engine, gateway
                ).dispatch_once(tenant_id)
                assert started is True
                start_row = start_intent_rows(bootstrap_engine, content_id)[0]
                assert start_row["status"] == INTENT_DELIVERED
                async with create_content_review_worker(env.client):
                    handle = env.client.get_workflow_handle(
                        start_row["temporal_workflow_id"]
                    )
                    state = await handle.query(ContentReviewWorkflowV1.state)
                    assert state["process_status"] == PROCESS_WAITING
                    approved = decide(
                        client,
                        tenant_id,
                        content_id,
                        version_id,
                        action="approve",
                        etag=submitted.headers["ETag"],
                        body={"reason_code": "reason_code_should_not_leak", "comment": "ok"},
                    )
                    assert approved.status_code == 200, approved.text
                    decision_id = approved.json()["review_decision_id"]
                    cmd = command_intent_rows(bootstrap_engine, content_id)[0]
                    handle = env.client.get_workflow_handle(
                        start_row["temporal_workflow_id"]
                    )
                    # Duplicate same command while waiting is idempotent.
                    await handle.signal(
                        "review_decision_recorded", dict(cmd["payload"])
                    )
                    await handle.signal(
                        "review_decision_recorded", dict(cmd["payload"])
                    )
                    result = await handle.result()
                    assert result["process_status"] == PROCESS_DECISION_OBSERVED
                    assert result["decision"] == "APPROVE"
                    assert result["review_decision_id"] == decision_id
                    # Crash-window style reconcile marks command DELIVERED.
                    delivered = await command_dispatcher(
                        workflow_dispatcher_engine, gateway
                    ).dispatch_once(tenant_id)
                    assert delivered is True
                    assert (
                        command_intent_rows(bootstrap_engine, content_id)[0]["status"]
                        == INTENT_DELIVERED
                    )
                    history = await handle.fetch_history()
                    blob = str(history.to_json_dict())
                    _assert_history_minimized(blob)
                    replayer = Replayer(workflows=[ContentReviewWorkflowV1])
                    await replayer.replay_workflow(history)

        run_async(scenario())

    def test_request_changes_and_reject_complete(
        self, runtime_engine, workflow_dispatcher_engine, bootstrap_engine
    ) -> None:
        async def scenario(action: str, body: dict[str, Any], decision: str) -> None:
            async with await WorkflowEnvironment.start_time_skipping() as env:
                async with create_content_review_worker(env.client):
                    tenant_id = uuid.uuid7()
                    client = client_for(runtime_engine, tenant_id, uuid.uuid7())
                    content_id, version_id, etag = in_review(client, tenant_id)
                    gateway = TemporalClientReviewGateway(env.client)
                    assert await start_dispatcher(
                        workflow_dispatcher_engine, gateway
                    ).dispatch_once(tenant_id)
                    decided = decide(
                        client,
                        tenant_id,
                        content_id,
                        version_id,
                        action=action,
                        etag=etag,
                        body=body,
                    )
                    assert decided.status_code == 200, decided.text
                    assert await command_dispatcher(
                        workflow_dispatcher_engine, gateway
                    ).dispatch_once(tenant_id)
                    start_row = start_intent_rows(bootstrap_engine, content_id)[0]
                    handle = env.client.get_workflow_handle(
                        start_row["temporal_workflow_id"]
                    )
                    result = await handle.result()
                    assert result["decision"] == decision

        run_async(scenario("request-changes", {"comment": "fix"}, "REQUEST_CHANGES"))
        run_async(scenario("reject", {}, "REJECT"))

    def test_command_before_start_stays_pending_then_delivers(
        self, runtime_engine, workflow_dispatcher_engine, bootstrap_engine
    ) -> None:
        async def scenario() -> None:
            async with await WorkflowEnvironment.start_time_skipping() as env:
                async with create_content_review_worker(env.client):
                    tenant_id = uuid.uuid7()
                    client = client_for(runtime_engine, tenant_id, uuid.uuid7())
                    content_id, version_id, etag = in_review(client, tenant_id)
                    approved = decide(
                        client,
                        tenant_id,
                        content_id,
                        version_id,
                        action="approve",
                        etag=etag,
                    )
                    assert approved.status_code == 200
                    gateway = TemporalClientReviewGateway(env.client)
                    # Command cannot deliver before start.
                    assert (
                        await command_dispatcher(
                            workflow_dispatcher_engine, gateway
                        ).dispatch_once(tenant_id)
                        is False
                    )
                    assert (
                        command_intent_rows(bootstrap_engine, content_id)[0]["status"]
                        == INTENT_PENDING
                    )
                    assert await start_dispatcher(
                        workflow_dispatcher_engine, gateway
                    ).dispatch_once(tenant_id)
                    assert await command_dispatcher(
                        workflow_dispatcher_engine, gateway
                    ).dispatch_once(tenant_id)
                    assert (
                        command_intent_rows(bootstrap_engine, content_id)[0]["status"]
                        == INTENT_DELIVERED
                    )

        run_async(scenario())

    def test_temporal_unavailable_after_submit_and_decision(
        self, runtime_engine, workflow_dispatcher_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = client_for(runtime_engine, tenant_id, uuid.uuid7())
        content_id, version_id, etag = generated_version(client, tenant_id)
        submitted = submit_review(client, tenant_id, content_id, version_id, etag=etag)
        assert submitted.status_code == 200
        assert content_row(bootstrap_engine, content_id).stewardship_state == "IN_REVIEW"
        assert start_intent_rows(bootstrap_engine, content_id)[0]["status"] == INTENT_PENDING

        async def fail_start() -> None:
            assert (
                await start_dispatcher(
                    workflow_dispatcher_engine, UnavailableGateway()
                ).dispatch_once(tenant_id)
                is False
            )

        run_async(fail_start())
        start_row = start_intent_rows(bootstrap_engine, content_id)[0]
        assert start_row["status"] == INTENT_PENDING
        assert start_row["last_error_code"] == ERROR_TEMPORAL_UNAVAILABLE
        assert content_row(bootstrap_engine, content_id).stewardship_state == "IN_REVIEW"

        approved = decide(
            client,
            tenant_id,
            content_id,
            version_id,
            action="approve",
            etag=submitted.headers["ETag"],
        )
        assert approved.status_code == 200
        assert decision_count(bootstrap_engine, content_id) == 1

        async def fail_command() -> None:
            # Start still pending → command not claimable.
            assert (
                await command_dispatcher(
                    workflow_dispatcher_engine, UnavailableGateway()
                ).dispatch_once(tenant_id)
                is False
            )

        run_async(fail_command())
        assert command_intent_rows(bootstrap_engine, content_id)[0]["status"] == INTENT_PENDING
        assert content_row(bootstrap_engine, content_id).stewardship_state == "APPROVED"

        async def recover() -> None:
            async with await WorkflowEnvironment.start_time_skipping() as env:
                async with create_content_review_worker(env.client):
                    gateway = TemporalClientReviewGateway(env.client)
                    assert await start_dispatcher(
                        workflow_dispatcher_engine, gateway
                    ).dispatch_once(tenant_id)
                    assert await command_dispatcher(
                        workflow_dispatcher_engine, gateway
                    ).dispatch_once(tenant_id)

        run_async(recover())
        assert start_intent_rows(bootstrap_engine, content_id)[0]["status"] == INTENT_DELIVERED
        assert command_intent_rows(bootstrap_engine, content_id)[0]["status"] == INTENT_DELIVERED

    def test_start_crash_window_reconciles_without_worker(
        self, runtime_engine, workflow_dispatcher_engine, bootstrap_engine
    ) -> None:
        async def scenario() -> None:
            async with await WorkflowEnvironment.start_time_skipping() as env:
                # No ContentReviewWorkflowV1 worker.
                tenant_id = uuid.uuid7()
                client = client_for(runtime_engine, tenant_id, uuid.uuid7())
                content_id, version_id, etag = generated_version(client, tenant_id)
                submit_review(client, tenant_id, content_id, version_id, etag=etag)
                gateway = TemporalClientReviewGateway(env.client)
                start_row = start_intent_rows(bootstrap_engine, content_id)[0]
                await env.client.start_workflow(
                    ContentReviewWorkflowV1.run,
                    dict(start_row["input"]),
                    id=start_row["temporal_workflow_id"],
                    task_queue=CONTENT_REVIEW_TASK_QUEUE,
                )
                assert await start_dispatcher(
                    workflow_dispatcher_engine, gateway
                ).dispatch_once(tenant_id)
                after = start_intent_rows(bootstrap_engine, content_id)[0]
                assert after["status"] == INTENT_DELIVERED
                assert after["temporal_workflow_id"] == start_row["temporal_workflow_id"]
                # Only after proof may a worker start.
                async with create_content_review_worker(env.client):
                    handle = env.client.get_workflow_handle(
                        after["temporal_workflow_id"]
                    )
                    state = await handle.query(ContentReviewWorkflowV1.state)
                    assert state["process_status"] == PROCESS_WAITING

        run_async(scenario())

    def test_command_crash_window_reconciles(
        self, runtime_engine, workflow_dispatcher_engine, bootstrap_engine
    ) -> None:
        async def scenario() -> None:
            async with await WorkflowEnvironment.start_time_skipping() as env:
                async with create_content_review_worker(env.client):
                    tenant_id = uuid.uuid7()
                    client = client_for(runtime_engine, tenant_id, uuid.uuid7())
                    content_id, version_id, etag = in_review(client, tenant_id)
                    gateway = TemporalClientReviewGateway(env.client)
                    assert await start_dispatcher(
                        workflow_dispatcher_engine, gateway
                    ).dispatch_once(tenant_id)
                    approved = decide(
                        client,
                        tenant_id,
                        content_id,
                        version_id,
                        action="approve",
                        etag=etag,
                    )
                    assert approved.status_code == 200
                    cmd = command_intent_rows(bootstrap_engine, content_id)[0]
                    start_row = start_intent_rows(bootstrap_engine, content_id)[0]
                    handle = env.client.get_workflow_handle(
                        start_row["temporal_workflow_id"]
                    )
                    await handle.signal("review_decision_recorded", dict(cmd["payload"]))
                    await handle.result()
                    # DB still PENDING; retry reconciles completed workflow.
                    assert await command_dispatcher(
                        workflow_dispatcher_engine, gateway
                    ).dispatch_once(tenant_id)
                    assert (
                        command_intent_rows(bootstrap_engine, content_id)[0]["status"]
                        == INTENT_DELIVERED
                    )

        run_async(scenario())

    def test_identity_mismatch_and_retry_exhaustion_quarantine(
        self, runtime_engine, workflow_dispatcher_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = client_for(runtime_engine, tenant_id, uuid.uuid7())
        content_id, version_id, etag = generated_version(client, tenant_id)
        submit_review(client, tenant_id, content_id, version_id, etag=etag)

        async def conflict() -> None:
            assert (
                await start_dispatcher(
                    workflow_dispatcher_engine, IdentityConflictGateway()
                ).dispatch_once(tenant_id)
                is False
            )

        run_async(conflict())
        row = start_intent_rows(bootstrap_engine, content_id)[0]
        assert row["status"] == INTENT_QUARANTINED
        assert row["last_error_code"] == "workflow_identity_conflict"

        tenant_b = uuid.uuid7()
        client_b = client_for(runtime_engine, tenant_b, uuid.uuid7())
        content_b, version_b, etag_b = generated_version(client_b, tenant_b)
        submit_review(client_b, tenant_b, content_b, version_b, etag=etag_b)
        repo = SqlAlchemyWorkflowDispatcherRepository(workflow_dispatcher_engine)
        config = DispatcherConfig(
            claim_lease=timedelta(seconds=1),
            max_attempts=1,
            retry_delay=timedelta(0),
            claimed_by="exhaust",
        )
        from aieos.platform.workflows.temporal.dispatchers import (
            ContentReviewStartDispatcher,
        )

        async def exhaust() -> None:
            dispatcher = ContentReviewStartDispatcher(
                repo, UnavailableGateway(), config
            )
            assert await dispatcher.dispatch_once(tenant_b) is False

        run_async(exhaust())
        exhausted = start_intent_rows(bootstrap_engine, content_b)[0]
        assert exhausted["status"] == INTENT_QUARANTINED
        assert exhausted["last_error_code"] == ERROR_RETRY_EXHAUSTED


class TestAuthorizationWhileWaiting:
    def test_revoked_authorization_blocks_decision_and_command(
        self, runtime_engine, workflow_dispatcher_engine, bootstrap_engine
    ) -> None:
        async def scenario() -> None:
            async with await WorkflowEnvironment.start_time_skipping() as env:
                async with create_content_review_worker(env.client):
                    tenant_id = uuid.uuid7()
                    submitter = uuid.uuid7()
                    reviewer = uuid.uuid7()
                    auth = AllowReviewAuthorization()
                    client = client_for(
                        runtime_engine, tenant_id, submitter, authorization=auth
                    )
                    content_id, version_id, etag = generated_version(client, tenant_id)
                    submitted = submit_review(
                        client, tenant_id, content_id, version_id, etag=etag
                    )
                    assert submitted.status_code == 200
                    gateway = TemporalClientReviewGateway(env.client)
                    assert await start_dispatcher(
                        workflow_dispatcher_engine, gateway
                    ).dispatch_once(tenant_id)
                    auth.allow_decide = False
                    reviewer_client = client_for(
                        runtime_engine, tenant_id, reviewer, authorization=auth
                    )
                    denied = decide(
                        reviewer_client,
                        tenant_id,
                        content_id,
                        version_id,
                        action="approve",
                        etag=submitted.headers["ETag"],
                    )
                    assert denied.status_code == 403
                    assert decision_count(bootstrap_engine, content_id) == 0
                    assert command_intent_rows(bootstrap_engine, content_id) == []
                    start_row = start_intent_rows(bootstrap_engine, content_id)[0]
                    handle = env.client.get_workflow_handle(
                        start_row["temporal_workflow_id"]
                    )
                    state = await handle.query(ContentReviewWorkflowV1.state)
                    assert state["process_status"] == PROCESS_WAITING
                    auth.allow_decide = True
                    allowed = decide(
                        reviewer_client,
                        tenant_id,
                        content_id,
                        version_id,
                        action="approve",
                        etag=submitted.headers["ETag"],
                    )
                    assert allowed.status_code == 200
                    assert await command_dispatcher(
                        workflow_dispatcher_engine, gateway
                    ).dispatch_once(tenant_id)
                    result = await handle.result()
                    assert result["decision"] == "APPROVE"

        run_async(scenario())


class TestCancellationAndTaskQueue:
    def test_cancellation_leaves_content_in_review(
        self, runtime_engine, workflow_dispatcher_engine, bootstrap_engine
    ) -> None:
        async def scenario() -> None:
            async with await WorkflowEnvironment.start_time_skipping() as env:
                async with create_content_review_worker(env.client):
                    tenant_a = uuid.uuid7()
                    tenant_b = uuid.uuid7()
                    client_a = client_for(runtime_engine, tenant_a, uuid.uuid7())
                    client_b = client_for(runtime_engine, tenant_b, uuid.uuid7())
                    content_a, version_a, etag_a = generated_version(client_a, tenant_a)
                    content_b, version_b, etag_b = generated_version(client_b, tenant_b)
                    submit_review(client_a, tenant_a, content_a, version_a, etag=etag_a)
                    submit_review(client_b, tenant_b, content_b, version_b, etag=etag_b)
                    gateway = TemporalClientReviewGateway(env.client)
                    assert await start_dispatcher(
                        workflow_dispatcher_engine, gateway
                    ).dispatch_once(tenant_a)
                    assert await start_dispatcher(
                        workflow_dispatcher_engine, gateway, claimed_by="d-b"
                    ).dispatch_once(tenant_b)
                    row_a = start_intent_rows(bootstrap_engine, content_a)[0]
                    row_b = start_intent_rows(bootstrap_engine, content_b)[0]
                    assert row_a["task_queue"] == CONTENT_REVIEW_TASK_QUEUE
                    assert row_b["task_queue"] == CONTENT_REVIEW_TASK_QUEUE
                    assert str(tenant_a) not in row_a["task_queue"]
                    handle = env.client.get_workflow_handle(row_a["temporal_workflow_id"])
                    await handle.cancel()
                    assert content_row(bootstrap_engine, content_a).stewardship_state == (
                        "IN_REVIEW"
                    )
                    assert decision_count(bootstrap_engine, content_a) == 0

        run_async(scenario())


def test_task_queue_constant_has_no_tenant() -> None:
    assert CONTENT_REVIEW_TASK_QUEUE == "aieos.content.review"
    assert "{" not in CONTENT_REVIEW_TASK_QUEUE

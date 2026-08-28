"""TOS-DEV03R2 lease heartbeat and failure timestamp proofs.

Requires real PostgreSQL (postgres18 / runtime_engine). Not in-memory.
"""

from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.engine import Engine

from aieos.domains.content.application.ai_for_review import (
    CreateAIGeneratedContentForReviewService,
)
from aieos.domains.content.application.errors import PersistenceOperationFailed
from aieos.platform.ai.clock import ControllableClock
from aieos.platform.ai.fake import FakeStructuredModelGateway
from aieos.platform.ai.infrastructure.persistence.uow import (
    SqlAlchemyAIUnitOfWorkFactory,
)
from tests.domains.teaching.helpers_dev03 import build_client, create_work, headers
from tests.domains.teaching.worksheet_fixtures import valid_worksheet_model

pytestmark = [pytest.mark.tos_dev03, pytest.mark.tos_dev03r2]

T0 = datetime(2026, 8, 28, 10, 0, 0, tzinfo=UTC)
LEASE_SECONDS = 120


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


class TestTosDev03R2FalseStaleAdversarial:
    def test_heartbeat_extends_lease_before_competing_request(
        self, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        clock = ControllableClock(T0)
        materialize_started = threading.Event()
        materialize_release = threading.Event()

        gateway = FakeStructuredModelGateway(
            result_factory=lambda _r: valid_worksheet_model(),
            before_generate=lambda: clock.advance(seconds=100),
        )
        client = build_client(
            runtime_engine,
            tenant_id,
            principal_id,
            model_gateway=gateway,
            generation_lease_seconds=LEASE_SECONDS,
            generation_clock=clock,
        )
        work_id, etag = _create_and_etag(client, tenant_id, key="r2-create-false-stale")

        original_create = CreateAIGeneratedContentForReviewService.create

        def _blocking_create(self, *args, **kwargs):
            materialize_started.set()
            assert materialize_release.wait(timeout=30)
            return original_create(self, *args, **kwargs)

        CreateAIGeneratedContentForReviewService.create = _blocking_create
        try:

            def _first_generate():
                return client.post(
                    f"/api/v1/teaching/works/{work_id}/actions/generate",
                    headers=headers(
                        tenant_id, idempotency_key="r2-gen-first", if_match=etag
                    ),
                )

            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(_first_generate)
                assert materialize_started.wait(timeout=30)

                heartbeat_at = T0 + timedelta(seconds=100)
                expected_lease = heartbeat_at + timedelta(seconds=LEASE_SECONDS)
                factory = SqlAlchemyAIUnitOfWorkFactory(runtime_engine)
                with factory(tenant_id) as uow:
                    active = uow.generation_runs.find_active_or_succeeded_for_work(
                        work_resource_id=uuid.UUID(work_id),
                    )
                    assert active is not None
                    assert active.lease_expires_at == expected_lease
                    assert active.updated_at == heartbeat_at

                clock.advance(seconds=30)
                competing_at = T0 + timedelta(seconds=130)
                assert clock() == competing_at

                competing = client.post(
                    f"/api/v1/teaching/works/{work_id}/actions/generate",
                    headers=headers(
                        tenant_id, idempotency_key="r2-gen-competing", if_match=etag
                    ),
                )
                assert competing.status_code == 409, competing.text
                assert competing.json()["code"] == "work_generation_in_progress"
                assert gateway.call_count == 1

                materialize_release.set()
                first = future.result(timeout=60)
        finally:
            CreateAIGeneratedContentForReviewService.create = original_create

        assert first.status_code == 200, first.text
        body = first.json()
        assert body["artifact"]["stewardship_state"] == "IN_REVIEW"

        with factory(tenant_id) as uow:
            runs = uow.generation_runs.list_for_work(
                principal_id=principal_id,
                work_resource_id=uuid.UUID(work_id),
            )
            assert len(runs) == 1
            assert runs[0].status.value == "SUCCEEDED"

        contents = client.get("/api/v1/contents", headers=headers(tenant_id))
        assert len(contents.json()["items"]) == 1

        version = client.get(
            f"/api/v1/contents/{body['artifact']['content_id']}/versions/"
            f"{body['artifact']['version_id']}",
            headers=headers(tenant_id),
        )
        assert version.status_code == 200, version.text

        queue = client.get(
            "/api/v1/teacher-os/review-queue",
            headers=headers(tenant_id),
        )
        assert queue.status_code == 200
        assert len(queue.json()["items"]) == 1
        assert gateway.call_count == 1


class TestTosDev03R2FailureTimestamp:
    def test_materialization_failure_uses_monotonic_timestamps(
        self, runtime_engine: Engine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        clock = ControllableClock(T0)
        gateway = FakeStructuredModelGateway(
            result_factory=lambda _r: valid_worksheet_model(),
            before_generate=lambda: clock.advance(seconds=100),
        )
        client = build_client(
            runtime_engine,
            tenant_id,
            principal_id,
            model_gateway=gateway,
            generation_lease_seconds=LEASE_SECONDS,
            generation_clock=clock,
        )
        work_id, etag = _create_and_etag(client, tenant_id, key="r2-create-fail-ts")

        def _boom(*_a, **_k):
            raise PersistenceOperationFailed("forced materialization failure")

        monkeypatch.setattr(
            CreateAIGeneratedContentForReviewService, "create", _boom
        )
        failed = client.post(
            f"/api/v1/teaching/works/{work_id}/actions/generate",
            headers=headers(tenant_id, idempotency_key="r2-gen-fail-ts", if_match=etag),
        )
        assert failed.status_code == 502, failed.text
        assert failed.json()["code"] == "content_materialization_failed"
        assert failed.json()["code"] != "model_generation_failed"

        heartbeat_at = T0 + timedelta(seconds=100)
        factory = SqlAlchemyAIUnitOfWorkFactory(runtime_engine)
        with factory(tenant_id) as uow:
            runs = uow.generation_runs.list_for_work(
                principal_id=principal_id,
                work_resource_id=uuid.UUID(work_id),
            )
            assert len(runs) == 1
            run = runs[0]
            assert run.status.value == "FAILED"
            assert run.failure_code == "content_materialization_failed"
            assert run.updated_at >= heartbeat_at
            assert run.completed_at is not None
            assert run.completed_at >= run.updated_at
            assert run.lease_expires_at is None

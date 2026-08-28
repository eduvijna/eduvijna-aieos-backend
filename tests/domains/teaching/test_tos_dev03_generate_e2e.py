"""TOS-DEV03 end-to-end fake-model generation and teacher isolation tests."""

from __future__ import annotations

import uuid

import pytest

from aieos.platform.ai.fake import FakeStructuredModelGateway
from aieos.platform.ai.gateway import ModelGenerationFailed, ModelProviderUnavailable
from tests.domains.teaching.helpers_dev03 import build_client, create_work, headers
from tests.domains.teaching.worksheet_fixtures import valid_worksheet_model

pytestmark = pytest.mark.tos_dev03


def _etag(response) -> str:
    return response.headers["ETag"]


class TestTosDev03GenerateE2E:
    def test_generate_creates_in_review_content_and_queue_item(
        self, runtime_engine, migrator_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        gateway = FakeStructuredModelGateway(
            result_factory=lambda _r: valid_worksheet_model()
        )
        client = build_client(
            runtime_engine, tenant_id, principal_id, model_gateway=gateway
        )
        created = create_work(
            client,
            tenant_id,
            goal_text=(
                "Tomorrow my Grade 5 students need to understand fractions "
                "using visual examples."
            ),
            target_date="2026-08-28",
            idempotency_key="create-1",
        )
        assert created.status_code == 201, created.text
        work_id = created.json()["work_id"]
        etag = _etag(created)

        generated = client.post(
            f"/api/v1/teaching/works/{work_id}/actions/generate",
            headers=headers(
                tenant_id, idempotency_key="gen-1", if_match=etag
            ),
        )
        assert generated.status_code == 200, generated.text
        body = generated.json()
        assert body["artifact"]["stewardship_state"] == "IN_REVIEW"
        assert body["artifact"]["content_type"] == "worksheet"
        assert body["educational_quality"]["status"] == "PASS"
        assert len(gateway.calls) == 1

        queue = client.get(
            "/api/v1/teacher-os/review-queue",
            headers=headers(tenant_id),
        )
        assert queue.status_code == 200
        items = queue.json()["items"]
        assert any(item["content_id"] == body["artifact"]["content_id"] for item in items)

        artifacts = client.get(
            f"/api/v1/teaching/works/{work_id}/artifacts",
            headers=headers(tenant_id),
        )
        assert artifacts.status_code == 200
        assert len(artifacts.json()["items"]) == 1
        assert artifacts.json()["items"][0]["origin"] == "AI"

        # Idempotent replay must not call the model again.
        replay = client.post(
            f"/api/v1/teaching/works/{work_id}/actions/generate",
            headers=headers(
                tenant_id, idempotency_key="gen-1", if_match=etag
            ),
        )
        assert replay.status_code == 200, replay.text
        assert replay.json()["generation_run_id"] == body["generation_run_id"]
        assert len(gateway.calls) == 1

        # Different key after success → already exists.
        again = client.post(
            f"/api/v1/teaching/works/{work_id}/actions/generate",
            headers=headers(
                tenant_id, idempotency_key="gen-2", if_match=etag
            ),
        )
        assert again.status_code == 409
        assert again.json()["code"] == "work_generation_already_exists"

    def test_educational_quality_failure_creates_no_content(
        self, runtime_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        gateway = FakeStructuredModelGateway(
            result_factory=lambda _r: valid_worksheet_model(
                include_alignment_claim=True
            )
        )
        client = build_client(
            runtime_engine, tenant_id, principal_id, model_gateway=gateway
        )
        created = create_work(
            client,
            tenant_id,
            goal_text="Understand fractions",
            target_date="2026-08-28",
            idempotency_key="create-eq",
        )
        work_id = created.json()["work_id"]
        failed = client.post(
            f"/api/v1/teaching/works/{work_id}/actions/generate",
            headers=headers(
                tenant_id,
                idempotency_key="gen-eq",
                if_match=_etag(created),
            ),
        )
        assert failed.status_code == 422
        assert failed.json()["code"] == "educational_quality_failed"

        contents = client.get("/api/v1/contents", headers=headers(tenant_id))
        assert contents.status_code == 200
        assert contents.json()["items"] == []

        artifacts = client.get(
            f"/api/v1/teaching/works/{work_id}/artifacts",
            headers=headers(tenant_id),
        )
        assert artifacts.json()["items"] == []

    def test_provider_failure_creates_no_content(self, runtime_engine) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        gateway = FakeStructuredModelGateway()
        gateway.fail_unavailable()
        client = build_client(
            runtime_engine, tenant_id, principal_id, model_gateway=gateway
        )
        created = create_work(
            client,
            tenant_id,
            goal_text="Understand fractions",
            target_date="2026-08-28",
            idempotency_key="create-prov",
        )
        work_id = created.json()["work_id"]
        failed = client.post(
            f"/api/v1/teaching/works/{work_id}/actions/generate",
            headers=headers(
                tenant_id,
                idempotency_key="gen-prov",
                if_match=_etag(created),
            ),
        )
        assert failed.status_code == 503
        assert failed.json()["code"] == "model_provider_unavailable"
        contents = client.get("/api/v1/contents", headers=headers(tenant_id))
        assert contents.json()["items"] == []

    def test_model_generation_failed_creates_no_content(
        self, runtime_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        gateway = FakeStructuredModelGateway(error=ModelGenerationFailed("boom"))
        client = build_client(
            runtime_engine, tenant_id, principal_id, model_gateway=gateway
        )
        created = create_work(
            client,
            tenant_id,
            goal_text="Understand fractions",
            target_date="2026-08-28",
            idempotency_key="create-fail",
        )
        work_id = created.json()["work_id"]
        failed = client.post(
            f"/api/v1/teaching/works/{work_id}/actions/generate",
            headers=headers(
                tenant_id,
                idempotency_key="gen-fail",
                if_match=_etag(created),
            ),
        )
        assert failed.status_code == 502
        assert failed.json()["code"] == "model_generation_failed"

    def test_teacher_isolation_same_tenant(self, runtime_engine) -> None:
        tenant_id = uuid.uuid7()
        teacher_a = uuid.uuid7()
        teacher_b = uuid.uuid7()
        gateway_a = FakeStructuredModelGateway(
            result_factory=lambda _r: valid_worksheet_model(title="A Worksheet")
        )
        client_a = build_client(
            runtime_engine, tenant_id, teacher_a, model_gateway=gateway_a
        )
        client_b = build_client(
            runtime_engine,
            tenant_id,
            teacher_b,
            model_gateway=FakeStructuredModelGateway(
                result_factory=lambda _r: valid_worksheet_model(title="B Worksheet")
            ),
        )

        created_a = create_work(
            client_a,
            tenant_id,
            goal_text="Teacher A goal",
            target_date="2026-08-28",
            idempotency_key="a-create",
        )
        work_a = created_a.json()["work_id"]
        gen_a = client_a.post(
            f"/api/v1/teaching/works/{work_a}/actions/generate",
            headers=headers(
                tenant_id,
                idempotency_key="a-gen",
                if_match=_etag(created_a),
            ),
        )
        assert gen_a.status_code == 200, gen_a.text
        content_a = gen_a.json()["artifact"]["content_id"]

        queue_a = client_a.get(
            "/api/v1/teacher-os/review-queue", headers=headers(tenant_id)
        )
        queue_b = client_b.get(
            "/api/v1/teacher-os/review-queue", headers=headers(tenant_id)
        )
        assert any(i["content_id"] == content_a for i in queue_a.json()["items"])
        assert all(i["content_id"] != content_a for i in queue_b.json()["items"])

        mission_a = client_a.get(
            "/api/v1/teacher-os/today/mission",
            params={"mission_date": "2026-08-28"},
            headers=headers(tenant_id),
        )
        mission_b = client_b.get(
            "/api/v1/teacher-os/today/mission",
            params={"mission_date": "2026-08-28"},
            headers=headers(tenant_id),
        )
        assert mission_a.json()["review"]["pending_count"] >= 1
        assert mission_b.json()["review"]["pending_count"] == 0
        assert mission_a.json()["hero_action"]["kind"] == "review"
        assert mission_b.json()["hero_action"]["kind"] != "review"

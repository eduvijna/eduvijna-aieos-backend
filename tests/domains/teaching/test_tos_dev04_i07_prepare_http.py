"""TOS-DEV04-I07 prepare HTTP contract and six-artifact Work projection."""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text

from aieos.domains.education.preparation_kit_v1 import PreparationKitV1
from aieos.domains.education.schema import PREPARATION_ARTIFACT_KINDS
from aieos.platform.ai.domain.generation_run import GenerationRunId, GenerationRunStatus
from aieos.platform.ai.fake import FakeStructuredModelGateway
from aieos.platform.ai.gateway import ModelProviderUnavailable
from aieos.platform.ai.infrastructure.persistence.uow import (
    SqlAlchemyAIUnitOfWorkFactory,
)
from tests.domains.teaching.helpers_dev03 import build_client, create_work, headers
from tests.domains.teaching.helpers_dev04_i06 import (
    pass_preparation_kit,
    quality_fail_preparation_kit,
)
from tests.domains.teaching.test_tos_dev04_i02_multi_artifact_persistence import (
    _clear_i02_downgrade_blockers,
)
from tests.domains.teaching.worksheet_fixtures import valid_worksheet_model
from tests.dbutil import REPO_ROOT

pytestmark = pytest.mark.tos_dev04_i07

CANONICAL_KINDS = list(PREPARATION_ARTIFACT_KINDS)


@pytest.fixture(autouse=True)
def _cleanup_i07_rows(postgres18: dict[str, str]) -> None:
    from sqlalchemy import create_engine

    yield
    engine = create_engine(postgres18["bootstrap_url"])
    try:
        _clear_i02_downgrade_blockers(engine)
    finally:
        engine.dispose()


def _etag(response) -> str:
    return response.headers["ETag"]


def _count(engine, sql: str, params: dict) -> int:
    with engine.connect() as conn:
        return int(conn.execute(text(sql), params).scalar_one())


def _prepare_gateway() -> FakeStructuredModelGateway:
    return FakeStructuredModelGateway(
        result_factory=lambda _req: pass_preparation_kit()
    )


class TestPrepareHappyPath:
    def test_prepare_exact_six(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        gateway = _prepare_gateway()
        client = build_client(
            runtime_engine, tenant_id, principal_id, model_gateway=gateway
        )
        created = create_work(client, tenant_id, idempotency_key="i07-create-1")
        assert created.status_code == 201, created.text
        work_id = created.json()["work_id"]
        etag = _etag(created)

        prepared = client.post(
            f"/api/v1/teaching/works/{work_id}/actions/prepare",
            headers=headers(tenant_id, idempotency_key="prep-1", if_match=etag),
        )
        assert prepared.status_code == 200, prepared.text
        body = prepared.json()
        assert body["preparation"]["status"] == "ready"
        assert len(body["artifacts"]) == 6
        assert [a["artifact_kind"] for a in body["artifacts"]] == CANONICAL_KINDS
        run_id = body["generation_run_id"]
        assert all(a["generation_run_id"] == run_id for a in body["artifacts"])
        assert body["educational_quality"]["status"] == "PASS"
        assert gateway.call_count == 1

        with SqlAlchemyAIUnitOfWorkFactory(runtime_engine)(tenant_id) as ai_uow:
            run = ai_uow.generation_runs.get(GenerationRunId(uuid.UUID(run_id)))
            assert run is not None
            assert run.status is GenerationRunStatus.SUCCEEDED
            assert run.result_content_id is None
            assert run.result_version_id is None
            assert run.result_content_revision is None

        assert (
            _count(
                bootstrap_engine,
                "SELECT count(*) FROM content.contents WHERE tenant_id = :tid",
                {"tid": tenant_id},
            )
            == 6
        )
        assert (
            _count(
                bootstrap_engine,
                """
                SELECT count(*) FROM content.content_versions
                 WHERE tenant_id = :tid AND origin = 'AI'
                   AND provenance #>> '{generation_run_ref,resource_id}' = :rid
                """,
                {"tid": tenant_id, "rid": run_id},
            )
            == 6
        )
        queue = client.get(
            "/api/v1/teacher-os/review-queue",
            headers=headers(tenant_id),
        )
        assert queue.status_code == 200
        queue_ids = {item["content_id"] for item in queue.json()["items"]}
        assert {a["content_id"] for a in body["artifacts"]} <= queue_ids


class TestPrepareReplayAndPreconditions:
    def test_same_key_replay(self, runtime_engine, bootstrap_engine) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        gateway = _prepare_gateway()
        client = build_client(
            runtime_engine, tenant_id, principal_id, model_gateway=gateway
        )
        created = create_work(client, tenant_id, idempotency_key="i07-create-2")
        work_id = created.json()["work_id"]
        etag = _etag(created)
        first = client.post(
            f"/api/v1/teaching/works/{work_id}/actions/prepare",
            headers=headers(tenant_id, idempotency_key="prep-replay", if_match=etag),
        )
        assert first.status_code == 200, first.text
        second = client.post(
            f"/api/v1/teaching/works/{work_id}/actions/prepare",
            headers=headers(tenant_id, idempotency_key="prep-replay", if_match=etag),
        )
        assert second.status_code == 200, second.text
        assert first.json()["generation_run_id"] == second.json()["generation_run_id"]
        assert [a["content_id"] for a in first.json()["artifacts"]] == [
            a["content_id"] for a in second.json()["artifacts"]
        ]
        assert [a["version_id"] for a in first.json()["artifacts"]] == [
            a["version_id"] for a in second.json()["artifacts"]
        ]
        assert gateway.call_count == 1
        assert (
            _count(
                bootstrap_engine,
                "SELECT count(*) FROM content.contents WHERE tenant_id = :tid",
                {"tid": tenant_id},
            )
            == 6
        )

    def test_missing_if_match_428(self, runtime_engine) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        client = build_client(
            runtime_engine, tenant_id, principal_id, model_gateway=_prepare_gateway()
        )
        created = create_work(client, tenant_id, idempotency_key="i07-create-3")
        work_id = created.json()["work_id"]
        response = client.post(
            f"/api/v1/teaching/works/{work_id}/actions/prepare",
            headers=headers(tenant_id, idempotency_key="prep-no-match"),
        )
        assert response.status_code == 428
        assert response.json()["code"] == "precondition_required"

    def test_stale_revision_412(self, runtime_engine) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        client = build_client(
            runtime_engine, tenant_id, principal_id, model_gateway=_prepare_gateway()
        )
        created = create_work(client, tenant_id, idempotency_key="i07-create-4")
        work_id = created.json()["work_id"]
        response = client.post(
            f"/api/v1/teaching/works/{work_id}/actions/prepare",
            headers=headers(
                tenant_id, idempotency_key="prep-stale", if_match='"r999"'
            ),
        )
        assert response.status_code == 412


class TestPrepareFailures:
    def test_idempotency_fingerprint_conflict(self, runtime_engine) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        client_a = build_client(
            runtime_engine,
            tenant_id,
            principal_id,
            model_gateway=_prepare_gateway(),
            provider_id="fake-a",
            model_id="model-a",
        )
        created = create_work(client_a, tenant_id, idempotency_key="i07-create-5")
        work_id = created.json()["work_id"]
        etag = _etag(created)
        first = client_a.post(
            f"/api/v1/teaching/works/{work_id}/actions/prepare",
            headers=headers(tenant_id, idempotency_key="prep-conflict", if_match=etag),
        )
        assert first.status_code == 200, first.text
        client_b = build_client(
            runtime_engine,
            tenant_id,
            principal_id,
            model_gateway=_prepare_gateway(),
            provider_id="fake-b",
            model_id="model-b",
        )
        conflict = client_b.post(
            f"/api/v1/teaching/works/{work_id}/actions/prepare",
            headers=headers(tenant_id, idempotency_key="prep-conflict", if_match=etag),
        )
        assert conflict.status_code == 409
        assert conflict.json()["code"] == "generation_idempotency_conflict"

    def test_educational_quality_failure(self, runtime_engine, bootstrap_engine) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        gateway = FakeStructuredModelGateway(
            result_factory=lambda _r: quality_fail_preparation_kit()
        )
        client = build_client(
            runtime_engine, tenant_id, principal_id, model_gateway=gateway
        )
        created = create_work(client, tenant_id, idempotency_key="i07-create-6")
        work_id = created.json()["work_id"]
        response = client.post(
            f"/api/v1/teaching/works/{work_id}/actions/prepare",
            headers=headers(
                tenant_id, idempotency_key="prep-eq", if_match=_etag(created)
            ),
        )
        assert response.status_code == 422
        assert response.json()["code"] == "educational_quality_failed"
        assert (
            _count(
                bootstrap_engine,
                "SELECT count(*) FROM content.contents WHERE tenant_id = :tid",
                {"tid": tenant_id},
            )
            == 0
        )

    def test_provider_failure_sanitized(self, runtime_engine) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        gateway = FakeStructuredModelGateway(
            error=ModelProviderUnavailable("secret-provider-token xyz")
        )
        client = build_client(
            runtime_engine, tenant_id, principal_id, model_gateway=gateway
        )
        created = create_work(client, tenant_id, idempotency_key="i07-create-7")
        work_id = created.json()["work_id"]
        response = client.post(
            f"/api/v1/teaching/works/{work_id}/actions/prepare",
            headers=headers(
                tenant_id, idempotency_key="prep-prov", if_match=_etag(created)
            ),
        )
        assert response.status_code == 503
        body = response.json()
        assert body["code"] == "model_provider_unavailable"
        dumped = json.dumps(body).lower()
        assert "secret-provider-token" not in dumped
        assert "xyz" not in dumped


class TestArtifactsProjectionAndIsolation:
    def test_artifacts_list_preparation_and_dev03(
        self, runtime_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        call = {"n": 0}

        def factory(request):
            call["n"] += 1
            # Preparation capability requests PreparationKitV1; worksheet
            # capability requests WorksheetV1 — branch on output_type.
            if request.output_type is PreparationKitV1:
                return pass_preparation_kit()
            return valid_worksheet_model()

        gateway = FakeStructuredModelGateway(result_factory=factory)
        client = build_client(
            runtime_engine, tenant_id, principal_id, model_gateway=gateway
        )
        created = create_work(client, tenant_id, idempotency_key="i07-create-8")
        work_id = created.json()["work_id"]
        etag = _etag(created)
        prepared = client.post(
            f"/api/v1/teaching/works/{work_id}/actions/prepare",
            headers=headers(tenant_id, idempotency_key="prep-art", if_match=etag),
        )
        assert prepared.status_code == 200, prepared.text

        # Separate work for DEV03 generate compatibility.
        created2 = create_work(client, tenant_id, idempotency_key="i07-create-8b")
        work2 = created2.json()["work_id"]
        generated = client.post(
            f"/api/v1/teaching/works/{work2}/actions/generate",
            headers=headers(
                tenant_id, idempotency_key="gen-art", if_match=_etag(created2)
            ),
        )
        assert generated.status_code == 200, generated.text

        prep_artifacts = client.get(
            f"/api/v1/teaching/works/{work_id}/artifacts",
            headers=headers(tenant_id),
        )
        assert prep_artifacts.status_code == 200
        items = prep_artifacts.json()["items"]
        assert len(items) == 6
        assert [i["artifact_kind"] for i in items] == CANONICAL_KINDS
        assert all(i["generation_run_id"] == prepared.json()["generation_run_id"] for i in items)

        gen_artifacts = client.get(
            f"/api/v1/teaching/works/{work2}/artifacts",
            headers=headers(tenant_id),
        )
        assert gen_artifacts.status_code == 200
        gen_items = gen_artifacts.json()["items"]
        assert len(gen_items) == 1
        assert gen_items[0]["content_type"] == "worksheet"
        assert gen_items[0]["generation_run_id"] == generated.json()["generation_run_id"]

    def test_same_tenant_teacher_isolation(self, runtime_engine) -> None:
        tenant_id = uuid.uuid7()
        teacher_a = uuid.uuid7()
        teacher_b = uuid.uuid7()
        client_a = build_client(
            runtime_engine, tenant_id, teacher_a, model_gateway=_prepare_gateway()
        )
        created = create_work(client_a, tenant_id, idempotency_key="i07-create-9")
        work_id = created.json()["work_id"]
        prepared = client_a.post(
            f"/api/v1/teaching/works/{work_id}/actions/prepare",
            headers=headers(
                tenant_id, idempotency_key="prep-iso", if_match=_etag(created)
            ),
        )
        assert prepared.status_code == 200, prepared.text

        client_b = build_client(
            runtime_engine, tenant_id, teacher_b, model_gateway=_prepare_gateway()
        )
        denied = client_b.post(
            f"/api/v1/teaching/works/{work_id}/actions/prepare",
            headers=headers(
                tenant_id, idempotency_key="prep-iso-b", if_match=_etag(created)
            ),
        )
        assert denied.status_code == 403
        listed = client_b.get(
            f"/api/v1/teaching/works/{work_id}/artifacts",
            headers=headers(tenant_id),
        )
        assert listed.status_code == 403

    def test_cross_tenant_denied(self, runtime_engine) -> None:
        tenant_a = uuid.uuid7()
        tenant_b = uuid.uuid7()
        principal = uuid.uuid7()
        client_a = build_client(
            runtime_engine, tenant_a, principal, model_gateway=_prepare_gateway()
        )
        created = create_work(client_a, tenant_a, idempotency_key="i07-create-10")
        work_id = created.json()["work_id"]
        client_b = build_client(
            runtime_engine, tenant_b, principal, model_gateway=_prepare_gateway()
        )
        denied = client_b.post(
            f"/api/v1/teaching/works/{work_id}/actions/prepare",
            headers=headers(
                tenant_b, idempotency_key="prep-xt", if_match=_etag(created)
            ),
        )
        assert denied.status_code in {403, 404}


class TestOpenApiPrepareContract:
    def test_prepare_and_generate_operations(self) -> None:
        spec = json.loads(
            (REPO_ROOT / "contracts" / "openapi" / "aieos-v1.json").read_text(
                encoding="utf-8"
            )
        )
        prepare = spec["paths"]["/api/v1/teaching/works/{work_id}/actions/prepare"][
            "post"
        ]
        generate = spec["paths"]["/api/v1/teaching/works/{work_id}/actions/generate"][
            "post"
        ]
        assert prepare["operationId"] == "teaching_work_prepare"
        assert generate["operationId"] == "teaching_work_generate"
        prepare_headers = {p["name"] for p in prepare.get("parameters", [])}
        assert "If-Match" in prepare_headers
        assert "Idempotency-Key" in prepare_headers
        schemas = spec["components"]["schemas"]
        assert "TeachingWorkPrepareResponse" in schemas
        artifact_item = schemas["WorkArtifactItemResponse"]["properties"]
        assert "artifact_kind" in artifact_item
        assert "generation_run_id" in artifact_item

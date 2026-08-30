"""TOS-DEV04-I07R1 Work artifact projection after review / later versions."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from aieos.domains.content.application.preparation_recovery import (
    PreparationBindingRecoveryStatus,
    inspect_preparation_generation_bindings,
)
from aieos.domains.content.infrastructure.persistence.uow import (
    SqlAlchemyContentUnitOfWorkFactory,
)
from aieos.domains.education.schema import (
    LESSON_PLAN_SCHEMA_ID,
    LESSON_PLAN_SCHEMA_VERSION,
    PREPARATION_ARTIFACT_KINDS,
)
from aieos.platform.ai.domain.generation_run import GenerationRunId
from aieos.platform.ai.fake import FakeStructuredModelGateway
from aieos.platform.ai.infrastructure.persistence.uow import (
    SqlAlchemyAIUnitOfWorkFactory,
)
from tests.domains.education.test_tos_dev04_i03_content_payloads import (
    valid_lesson_plan_payload,
)
from tests.domains.teaching.helpers_dev03 import build_client, create_work, headers
from tests.domains.teaching.helpers_dev04_i06 import pass_preparation_kit
from tests.domains.teaching.test_tos_dev04_i02_multi_artifact_persistence import (
    _clear_i02_downgrade_blockers,
)

pytestmark = pytest.mark.tos_dev04_i07

CANONICAL_KINDS = list(PREPARATION_ARTIFACT_KINDS)


@pytest.fixture(autouse=True)
def _cleanup_i07r1_rows(postgres18: dict[str, str]) -> None:
    from sqlalchemy import create_engine

    yield
    engine = create_engine(postgres18["bootstrap_url"])
    try:
        _clear_i02_downgrade_blockers(engine)
    finally:
        engine.dispose()


def _etag(response) -> str:
    return response.headers["ETag"]


def _prepare_gateway() -> FakeStructuredModelGateway:
    return FakeStructuredModelGateway(
        result_factory=lambda _req: pass_preparation_kit()
    )


def _prepare_work(client, tenant_id: uuid.UUID, *, create_key: str, prep_key: str):
    created = create_work(client, tenant_id, idempotency_key=create_key)
    assert created.status_code == 201, created.text
    work_id = created.json()["work_id"]
    prepared = client.post(
        f"/api/v1/teaching/works/{work_id}/actions/prepare",
        headers=headers(
            tenant_id, idempotency_key=prep_key, if_match=_etag(created)
        ),
    )
    assert prepared.status_code == 200, prepared.text
    return work_id, prepared.json()


def _approve_artifact(client, tenant_id: uuid.UUID, content_id: str, version_id: str):
    detail = client.get(
        f"/api/v1/teacher-os/review-queue/{content_id}/versions/{version_id}",
        headers=headers(tenant_id),
    )
    assert detail.status_code == 200, detail.text
    approved = client.post(
        f"/api/v1/contents/{content_id}/versions/{version_id}/actions/approve",
        json={},
        headers={
            **headers(tenant_id, idempotency_key=f"approve-{content_id}"),
            "If-Match": _etag(detail),
        },
    )
    assert approved.status_code == 200, approved.text
    return approved


class TestPostApprovalProjection:
    def test_artifacts_remain_after_approve(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        client = build_client(
            runtime_engine, tenant_id, principal_id, model_gateway=_prepare_gateway()
        )
        work_id, prepared = _prepare_work(
            client, tenant_id, create_key="i07r1-create-1", prep_key="i07r1-prep-1"
        )
        run_id = prepared["generation_run_id"]
        original = {
            a["artifact_kind"]: {
                "content_id": a["content_id"],
                "version_id": a["version_id"],
                "generation_run_id": a["generation_run_id"],
            }
            for a in prepared["artifacts"]
        }
        target = next(a for a in prepared["artifacts"] if a["artifact_kind"] == "quiz")
        _approve_artifact(
            client, tenant_id, target["content_id"], target["version_id"]
        )

        content = client.get(
            f"/api/v1/contents/{target['content_id']}",
            headers=headers(tenant_id),
        )
        assert content.status_code == 200, content.text
        assert content.json()["stewardship_state"] == "APPROVED"
        assert content.json()["stewardship_state"] != "IN_REVIEW"

        queue = client.get(
            "/api/v1/teacher-os/review-queue",
            headers=headers(tenant_id),
        )
        assert queue.status_code == 200
        queue_ids = {item["content_id"] for item in queue.json()["items"]}
        assert target["content_id"] not in queue_ids

        listed = client.get(
            f"/api/v1/teaching/works/{work_id}/artifacts",
            headers=headers(tenant_id),
        )
        assert listed.status_code == 200, listed.text
        assert "preparation_recovery_invariant" not in listed.text
        items = listed.json()["items"]
        assert len(items) == 6
        assert [i["artifact_kind"] for i in items] == CANONICAL_KINDS
        assert all(i["generation_run_id"] == run_id for i in items)
        for item in items:
            snap = original[item["artifact_kind"]]
            assert item["content_id"] == snap["content_id"]
            assert item["version_id"] == snap["version_id"]
            assert item["generation_run_id"] == snap["generation_run_id"]
        approved_item = next(i for i in items if i["artifact_kind"] == "quiz")
        assert approved_item["stewardship_state"] == "APPROVED"
        assert approved_item["version_id"] == target["version_id"]

        # I06 recovery inspection must remain strict (still requires IN_REVIEW).
        with SqlAlchemyAIUnitOfWorkFactory(runtime_engine)(tenant_id) as ai_uow:
            run = ai_uow.generation_runs.get(GenerationRunId(uuid.UUID(run_id)))
            assert run is not None
        with SqlAlchemyContentUnitOfWorkFactory(runtime_engine)(tenant_id) as cuow:
            inspection = inspect_preparation_generation_bindings(cuow, run)
        assert inspection.status is PreparationBindingRecoveryStatus.INVALID
        assert inspection.detail is not None
        assert "IN_REVIEW" in inspection.detail


class TestLaterVersionProjection:
    def test_historical_version_retained_after_append(
        self, runtime_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        client = build_client(
            runtime_engine, tenant_id, principal_id, model_gateway=_prepare_gateway()
        )
        work_id, prepared = _prepare_work(
            client, tenant_id, create_key="i07r1-create-2", prep_key="i07r1-prep-2"
        )
        run_id = prepared["generation_run_id"]
        target = next(
            a for a in prepared["artifacts"] if a["artifact_kind"] == "lesson_plan"
        )
        generated_version_id = target["version_id"]
        approved = _approve_artifact(
            client, tenant_id, target["content_id"], generated_version_id
        )

        appended = client.post(
            f"/api/v1/contents/{target['content_id']}/versions",
            json={
                "schema_id": LESSON_PLAN_SCHEMA_ID,
                "schema_version": LESSON_PLAN_SCHEMA_VERSION,
                "payload": valid_lesson_plan_payload(title="Later lesson plan edit"),
            },
            headers={
                **headers(tenant_id, idempotency_key="i07r1-append"),
                "If-Match": _etag(approved),
            },
        )
        assert appended.status_code == 201, appended.text
        later_version_id = appended.json()["version_id"]
        assert later_version_id != generated_version_id

        content = client.get(
            f"/api/v1/contents/{target['content_id']}",
            headers=headers(tenant_id),
        )
        assert content.status_code == 200, content.text
        body = content.json()
        assert body["current_version_id"] == later_version_id
        assert body["current_version_id"] != generated_version_id

        listed = client.get(
            f"/api/v1/teaching/works/{work_id}/artifacts",
            headers=headers(tenant_id),
        )
        assert listed.status_code == 200, listed.text
        items = listed.json()["items"]
        assert len(items) == 6
        assert [i["artifact_kind"] for i in items] == CANONICAL_KINDS
        lesson = next(i for i in items if i["artifact_kind"] == "lesson_plan")
        assert lesson["version_id"] == generated_version_id
        assert lesson["version_id"] != later_version_id
        assert lesson["generation_run_id"] == run_id
        assert lesson["content_id"] == target["content_id"]
        assert lesson["title"] == body["title"]
        assert lesson["stewardship_state"] == body["stewardship_state"]
        assert lesson["aggregate_revision"] == body["aggregate_revision"]

        with SqlAlchemyAIUnitOfWorkFactory(runtime_engine)(tenant_id) as ai_uow:
            run = ai_uow.generation_runs.get(GenerationRunId(uuid.UUID(run_id)))
            assert run is not None
        with SqlAlchemyContentUnitOfWorkFactory(runtime_engine)(tenant_id) as cuow:
            inspection = inspect_preparation_generation_bindings(cuow, run)
        assert inspection.status is PreparationBindingRecoveryStatus.INVALID
        assert inspection.detail is not None
        assert "current_version_id" in inspection.detail


class TestCorruptProjectionFailsClosed:
    def test_missing_binding_fails_closed(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        client = build_client(
            runtime_engine, tenant_id, principal_id, model_gateway=_prepare_gateway()
        )
        work_id, prepared = _prepare_work(
            client, tenant_id, create_key="i07r1-create-3", prep_key="i07r1-prep-3"
        )
        run_id = prepared["generation_run_id"]
        with bootstrap_engine.begin() as conn:
            conn.execute(text("SET LOCAL session_replication_role = replica"))
            conn.execute(
                text(
                    """
                    DELETE FROM content.content_versions
                     WHERE tenant_id = :tid
                       AND provenance #>> '{generation_run_ref,resource_id}' = :rid
                       AND (provenance->>'artifact_kind') = 'teacher_notes'
                    """
                ),
                {"tid": tenant_id, "rid": run_id},
            )

        listed = client.get(
            f"/api/v1/teaching/works/{work_id}/artifacts",
            headers=headers(tenant_id),
        )
        assert listed.status_code == 422, listed.text
        assert listed.json()["code"] == "preparation_recovery_invariant_violation"

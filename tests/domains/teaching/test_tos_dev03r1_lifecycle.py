"""TOS-DEV03R1 lifecycle, fence, recovery, and failure classification proofs.

Requires real PostgreSQL (postgres18 / runtime_engine). Not in-memory.
"""

from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.platform.ai.domain.generation_run import (
    GenerationRunId,
    GenerationRunStatus,
)
from aieos.platform.ai.fake import FakeStructuredModelGateway
from aieos.platform.ai.gateway import ModelGenerationFailed, ModelProviderUnavailable
from aieos.platform.ai.infrastructure.persistence.uow import (
    SqlAlchemyAIUnitOfWorkFactory,
)
from tests.conftest import alembic_config, provision_runtime_grants
from tests.domains.teaching.helpers_dev03 import build_client, create_work, headers
from tests.domains.teaching.worksheet_fixtures import valid_worksheet_model

pytestmark = [pytest.mark.tos_dev03, pytest.mark.tos_dev03r1]

FIXED_NOW = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)


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


class TestTosDev03R1HappyPathAndIdempotency:
    def test_no_durable_validated_on_success(
        self, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        gateway = FakeStructuredModelGateway(
            result_factory=lambda _r: valid_worksheet_model()
        )
        client = build_client(
            runtime_engine, tenant_id, principal_id, model_gateway=gateway
        )
        work_id, etag = _create_and_etag(client, tenant_id, key="r1-create-noval")
        generated = client.post(
            f"/api/v1/teaching/works/{work_id}/actions/generate",
            headers=headers(tenant_id, idempotency_key="r1-gen-noval", if_match=etag),
        )
        assert generated.status_code == 200, generated.text
        body = generated.json()
        assert body["artifact"]["stewardship_state"] == "IN_REVIEW"
        assert gateway.call_count == 1

        factory = SqlAlchemyAIUnitOfWorkFactory(runtime_engine)
        with factory(tenant_id) as uow:
            runs = uow.generation_runs.list_for_work(
                principal_id=principal_id,
                work_resource_id=uuid.UUID(work_id),
            )
            assert len(runs) == 1
            assert runs[0].status is GenerationRunStatus.SUCCEEDED
            assert runs[0].status is not GenerationRunStatus.VALIDATED

        with runtime_engine.connect() as conn:
            conn.execute(
                text("SELECT set_config('aieos.tenant_id', :tid, true)"),
                {"tid": str(tenant_id)},
            )
            validated = conn.execute(
                text(
                    """
                    SELECT count(*) FROM ai.generation_runs
                    WHERE tenant_id = :tid AND status = 'VALIDATED'
                    """
                ),
                {"tid": tenant_id},
            ).scalar_one()
            assert int(validated) == 0

    def test_same_key_replay_same_ids(self, runtime_engine: Engine) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        gateway = FakeStructuredModelGateway(
            result_factory=lambda _r: valid_worksheet_model()
        )
        client = build_client(
            runtime_engine, tenant_id, principal_id, model_gateway=gateway
        )
        work_id, etag = _create_and_etag(client, tenant_id, key="r1-create-replay")
        first = client.post(
            f"/api/v1/teaching/works/{work_id}/actions/generate",
            headers=headers(tenant_id, idempotency_key="r1-gen-replay", if_match=etag),
        )
        assert first.status_code == 200, first.text
        replay = client.post(
            f"/api/v1/teaching/works/{work_id}/actions/generate",
            headers=headers(tenant_id, idempotency_key="r1-gen-replay", if_match=etag),
        )
        assert replay.status_code == 200, replay.text
        assert replay.json()["generation_run_id"] == first.json()["generation_run_id"]
        assert (
            replay.json()["artifact"]["content_id"]
            == first.json()["artifact"]["content_id"]
        )
        assert (
            replay.json()["artifact"]["version_id"]
            == first.json()["artifact"]["version_id"]
        )
        assert gateway.call_count == 1

    def test_different_key_after_success_already_exists(
        self, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        gateway = FakeStructuredModelGateway(
            result_factory=lambda _r: valid_worksheet_model()
        )
        client = build_client(
            runtime_engine, tenant_id, principal_id, model_gateway=gateway
        )
        work_id, etag = _create_and_etag(client, tenant_id, key="r1-create-exists")
        assert (
            client.post(
                f"/api/v1/teaching/works/{work_id}/actions/generate",
                headers=headers(
                    tenant_id, idempotency_key="r1-gen-exists-1", if_match=etag
                ),
            ).status_code
            == 200
        )
        again = client.post(
            f"/api/v1/teaching/works/{work_id}/actions/generate",
            headers=headers(
                tenant_id, idempotency_key="r1-gen-exists-2", if_match=etag
            ),
        )
        assert again.status_code == 409
        assert again.json()["code"] == "work_generation_already_exists"


class TestTosDev03R1Failures:
    def test_eq_failure_creates_no_content(self, runtime_engine: Engine) -> None:
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
        work_id, etag = _create_and_etag(client, tenant_id, key="r1-create-eq")
        failed = client.post(
            f"/api/v1/teaching/works/{work_id}/actions/generate",
            headers=headers(tenant_id, idempotency_key="r1-gen-eq", if_match=etag),
        )
        assert failed.status_code == 422
        assert failed.json()["code"] == "educational_quality_failed"
        contents = client.get("/api/v1/contents", headers=headers(tenant_id))
        assert contents.json()["items"] == []

    def test_provider_failure_stays_provider_code(
        self, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        gateway = FakeStructuredModelGateway()
        gateway.fail_unavailable()
        client = build_client(
            runtime_engine, tenant_id, principal_id, model_gateway=gateway
        )
        work_id, etag = _create_and_etag(client, tenant_id, key="r1-create-prov")
        failed = client.post(
            f"/api/v1/teaching/works/{work_id}/actions/generate",
            headers=headers(tenant_id, idempotency_key="r1-gen-prov", if_match=etag),
        )
        assert failed.status_code == 503
        assert failed.json()["code"] == "model_provider_unavailable"

    def test_model_generation_failed_distinct(
        self, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        gateway = FakeStructuredModelGateway(error=ModelGenerationFailed("boom"))
        client = build_client(
            runtime_engine, tenant_id, principal_id, model_gateway=gateway
        )
        work_id, etag = _create_and_etag(client, tenant_id, key="r1-create-mgf")
        failed = client.post(
            f"/api/v1/teaching/works/{work_id}/actions/generate",
            headers=headers(tenant_id, idempotency_key="r1-gen-mgf", if_match=etag),
        )
        assert failed.status_code == 502
        assert failed.json()["code"] == "model_generation_failed"

    def test_content_materialization_failure_not_model_failed(
        self, runtime_engine: Engine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from aieos.domains.content.application.ai_for_review import (
            CreateAIGeneratedContentForReviewService,
        )
        from aieos.domains.content.application.errors import PersistenceOperationFailed

        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        gateway = FakeStructuredModelGateway(
            result_factory=lambda _r: valid_worksheet_model()
        )
        client = build_client(
            runtime_engine, tenant_id, principal_id, model_gateway=gateway
        )
        work_id, etag = _create_and_etag(client, tenant_id, key="r1-create-mat")

        def _boom(*_a, **_k):
            raise PersistenceOperationFailed("forced materialization failure")

        monkeypatch.setattr(
            CreateAIGeneratedContentForReviewService, "create", _boom
        )
        failed = client.post(
            f"/api/v1/teaching/works/{work_id}/actions/generate",
            headers=headers(tenant_id, idempotency_key="r1-gen-mat", if_match=etag),
        )
        assert failed.status_code == 502
        assert failed.json()["code"] == "content_materialization_failed"
        assert failed.json()["code"] != "model_generation_failed"

    def test_failed_then_new_key_retry_succeeds(
        self, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        gateway = FakeStructuredModelGateway(error=ModelProviderUnavailable("down"))
        client = build_client(
            runtime_engine, tenant_id, principal_id, model_gateway=gateway
        )
        work_id, etag = _create_and_etag(client, tenant_id, key="r1-create-retry")
        failed = client.post(
            f"/api/v1/teaching/works/{work_id}/actions/generate",
            headers=headers(tenant_id, idempotency_key="r1-gen-retry-1", if_match=etag),
        )
        assert failed.status_code == 503
        gateway.error = None
        gateway.result_factory = lambda _r: valid_worksheet_model()
        ok = client.post(
            f"/api/v1/teaching/works/{work_id}/actions/generate",
            headers=headers(tenant_id, idempotency_key="r1-gen-retry-2", if_match=etag),
        )
        assert ok.status_code == 200, ok.text
        assert ok.json()["artifact"]["stewardship_state"] == "IN_REVIEW"


class TestTosDev03R1Concurrency:
    def test_concurrent_different_keys_single_model_call(
        self, runtime_engine: Engine
    ) -> None:
        import time

        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        gateway = FakeStructuredModelGateway(
            result_factory=lambda _r: valid_worksheet_model(),
            before_generate=lambda: time.sleep(0.35),
        )
        client = build_client(
            runtime_engine, tenant_id, principal_id, model_gateway=gateway
        )
        work_id, etag = _create_and_etag(client, tenant_id, key="r1-create-conc-diff")
        results: list = []

        def _worker(key: str) -> None:
            results.append(
                client.post(
                    f"/api/v1/teaching/works/{work_id}/actions/generate",
                    headers=headers(tenant_id, idempotency_key=key, if_match=etag),
                )
            )

        t1 = threading.Thread(target=_worker, args=("r1-diff-a",))
        t2 = threading.Thread(target=_worker, args=("r1-diff-b",))
        t1.start()
        t2.start()
        t1.join(timeout=60)
        t2.join(timeout=60)
        assert len(results) == 2
        ok = [r for r in results if r.status_code == 200]
        blocked = [
            r
            for r in results
            if r.status_code == 409
            and r.json().get("code")
            in ("work_generation_in_progress", "work_generation_already_exists")
        ]
        assert len(ok) == 1, [(r.status_code, r.text) for r in results]
        assert len(blocked) == 1, [(r.status_code, r.text) for r in results]
        assert gateway.call_count == 1

        contents = client.get("/api/v1/contents", headers=headers(tenant_id))
        assert len(contents.json()["items"]) == 1
        queue = client.get(
            "/api/v1/teacher-os/review-queue", headers=headers(tenant_id)
        )
        assert queue.status_code == 200
        assert len(queue.json()["items"]) == 1

    def test_concurrent_same_key_single_model_call(
        self, runtime_engine: Engine
    ) -> None:
        import time

        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        gateway = FakeStructuredModelGateway(
            result_factory=lambda _r: valid_worksheet_model(),
            before_generate=lambda: time.sleep(0.35),
        )
        client = build_client(
            runtime_engine, tenant_id, principal_id, model_gateway=gateway
        )
        work_id, etag = _create_and_etag(client, tenant_id, key="r1-create-conc-same")

        def _call():
            return client.post(
                f"/api/v1/teaching/works/{work_id}/actions/generate",
                headers=headers(
                    tenant_id, idempotency_key="r1-same-key", if_match=etag
                ),
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(_call), pool.submit(_call)]
            responses = [f.result(timeout=60) for f in as_completed(futures)]

        assert gateway.call_count == 1
        assert all(r.status_code in (200, 409) for r in responses), [
            (r.status_code, r.text) for r in responses
        ]
        assert any(r.status_code == 200 for r in responses)


class TestTosDev03R1Recovery:
    def test_crash_after_content_before_succeeded_recovers(
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
        work_id, etag = _create_and_etag(client, tenant_id, key="r1-create-crash")
        generated = client.post(
            f"/api/v1/teaching/works/{work_id}/actions/generate",
            headers=headers(tenant_id, idempotency_key="r1-gen-crash", if_match=etag),
        )
        assert generated.status_code == 200, generated.text
        body = generated.json()
        run_id = uuid.UUID(body["generation_run_id"])
        content_id = body["artifact"]["content_id"]
        version_id = body["artifact"]["version_id"]
        assert gateway.call_count == 1

        factory = SqlAlchemyAIUnitOfWorkFactory(runtime_engine)
        with factory(tenant_id) as uow:
            locked = uow.generation_runs.get_for_update(GenerationRunId(run_id))
            assert locked is not None
            stale_at = locked.updated_at
            crashed = replace(
                locked,
                status=GenerationRunStatus.RUNNING,
                result_content_id=None,
                result_version_id=None,
                result_content_revision=None,
                completed_at=None,
                lease_expires_at=stale_at - timedelta(seconds=30),
                updated_at=stale_at,
                aggregate_revision=locked.aggregate_revision + 1,
            )
            assert uow.generation_runs.update(
                crashed, expected_revision=locked.aggregate_revision
            )
            uow.commit()

        recovered = client.post(
            f"/api/v1/teaching/works/{work_id}/actions/generate",
            headers=headers(tenant_id, idempotency_key="r1-gen-crash", if_match=etag),
        )
        assert recovered.status_code == 200, recovered.text
        assert recovered.json()["generation_run_id"] == str(run_id)
        assert recovered.json()["artifact"]["content_id"] == content_id
        assert recovered.json()["artifact"]["version_id"] == version_id
        assert gateway.call_count == 1

        contents = client.get("/api/v1/contents", headers=headers(tenant_id))
        assert len(contents.json()["items"]) == 1

    def test_generation_run_id_uniqueness_reconciles(
        self, runtime_engine: Engine
    ) -> None:
        from aieos.domains.content.application.ai_for_review import (
            CreateAIGeneratedContentForReviewCommand,
            CreateAIGeneratedContentForReviewService,
            find_ai_version_by_generation_run,
        )
        from aieos.domains.content.application.audit import (
            ai_materialization_audit_provenance,
        )
        from aieos.domains.content.domain.provenance import AIGenerationProvenanceV1
        from aieos.domains.content.infrastructure.persistence.uow import (
            SqlAlchemyContentUnitOfWorkFactory,
        )
        from aieos.domains.education.schema import (
            WORKSHEET_CONTENT_TYPE,
            WORKSHEET_SCHEMA_ID,
            WORKSHEET_SCHEMA_VERSION,
        )
        from aieos.development.schemas import build_development_schema_registry
        from aieos.domains.content.application.catalog import StaticContentTypeCatalog
        from aieos.platform.events.models import MutationEventContext
        from aieos.platform.resources import ResourceRef
        from tests.fakes import (
            AllowAIGenerationAuthorization,
            AllowAssetReferenceValidation,
        )

        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        run_id = uuid.uuid7()
        service = CreateAIGeneratedContentForReviewService(
            SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
            StaticContentTypeCatalog({WORKSHEET_CONTENT_TYPE}),
            build_development_schema_registry(),
            AllowAssetReferenceValidation(),
            AllowAIGenerationAuthorization(),
        )
        worksheet = valid_worksheet_model()
        correlation_id = uuid.uuid7()
        provenance = AIGenerationProvenanceV1(
            generation_run_ref=ResourceRef("ai.generation_run", run_id, 0),
            prompt_execution_ref=None,
            provider_id="fake",
            model_id="fake-model",
            capability_id="education.generate_worksheet",
            source_refs=(),
            policy_refs=(),
            evaluation_refs=(),
            correlation_id=correlation_id,
        )
        event_context = MutationEventContext(
            correlation_id=correlation_id,
            causation_id=uuid.uuid7(),
            actor_principal_id=principal_id,
            effective_actor_id=principal_id,
        )
        first = service.create(
            tenant_id,
            principal_id,
            CreateAIGeneratedContentForReviewCommand(
                content_type=WORKSHEET_CONTENT_TYPE,
                title=worksheet.title,
                description=worksheet.teacher_summary,
                locale="en-IN",
                schema_id=WORKSHEET_SCHEMA_ID,
                schema_version=WORKSHEET_SCHEMA_VERSION,
                payload=worksheet.model_dump(mode="json"),
                provenance=provenance,
            ),
            event_context=event_context,
            audit_provenance=ai_materialization_audit_provenance(principal_id),
            now=FIXED_NOW,
        )
        second = service.create(
            tenant_id,
            principal_id,
            CreateAIGeneratedContentForReviewCommand(
                content_type=WORKSHEET_CONTENT_TYPE,
                title=worksheet.title,
                description=worksheet.teacher_summary,
                locale="en-IN",
                schema_id=WORKSHEET_SCHEMA_ID,
                schema_version=WORKSHEET_SCHEMA_VERSION,
                payload=worksheet.model_dump(mode="json"),
                provenance=provenance,
            ),
            event_context=event_context,
            audit_provenance=ai_materialization_audit_provenance(principal_id),
            now=FIXED_NOW,
        )
        assert second.content_id == first.content_id
        assert second.version_id == first.version_id
        with SqlAlchemyContentUnitOfWorkFactory(runtime_engine)(tenant_id) as uow:
            found = find_ai_version_by_generation_run(uow, run_id)
            assert found is not None
            assert found.version_id == first.version_id


class TestTosDev03R1TeacherIsolationAndArtifact:
    def test_teacher_isolation_and_in_review_artifact(
        self, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        teacher_a = uuid.uuid7()
        teacher_b = uuid.uuid7()
        gateway_a = FakeStructuredModelGateway(
            result_factory=lambda _r: valid_worksheet_model()
        )
        client_a = build_client(
            runtime_engine, tenant_id, teacher_a, model_gateway=gateway_a
        )
        work_id, etag = _create_and_etag(client_a, tenant_id, key="r1-create-iso")
        generated = client_a.post(
            f"/api/v1/teaching/works/{work_id}/actions/generate",
            headers=headers(tenant_id, idempotency_key="r1-gen-iso", if_match=etag),
        )
        assert generated.status_code == 200, generated.text
        assert generated.json()["artifact"]["stewardship_state"] == "IN_REVIEW"

        client_b = build_client(
            runtime_engine,
            tenant_id,
            teacher_b,
            model_gateway=FakeStructuredModelGateway(
                result_factory=lambda _r: valid_worksheet_model()
            ),
        )
        forbidden = client_b.post(
            f"/api/v1/teaching/works/{work_id}/actions/generate",
            headers=headers(tenant_id, idempotency_key="r1-gen-iso-b", if_match=etag),
        )
        assert forbidden.status_code in (403, 404)


class TestTosDev03R1Migration:
    def test_upgrade_downgrade_reupgrade_tosd030002(
        self, postgres18, bootstrap_engine: Engine
    ) -> None:
        cfg = alembic_config(postgres18["migrator_url"])
        with bootstrap_engine.begin() as conn:
            conn.execute(text("DELETE FROM ai.generation_runs"))
        command.downgrade(cfg, "tosd030001")
        with bootstrap_engine.connect() as conn:
            assert (
                conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "tosd030001"
            )
            lease = conn.execute(
                text(
                    """
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema = 'ai'
                      AND table_name = 'generation_runs'
                      AND column_name = 'lease_expires_at'
                    """
                )
            ).scalar_one_or_none()
            assert lease is None
        command.upgrade(cfg, "head")
        provision_runtime_grants(bootstrap_engine)
        with bootstrap_engine.connect() as conn:
            assert (
                conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "tosd030002"
            )
            fence = conn.execute(
                text(
                    """
                    SELECT 1 FROM pg_indexes
                    WHERE schemaname = 'ai'
                      AND indexname = 'uq_ai_generation_runs_work_active_or_succeeded'
                    """
                )
            ).scalar_one_or_none()
            assert fence == 1
            binding = conn.execute(
                text(
                    """
                    SELECT 1 FROM pg_indexes
                    WHERE schemaname = 'content'
                      AND indexname = 'uq_content_versions_ai_generation_run_id'
                    """
                )
            ).scalar_one_or_none()
            assert binding == 1

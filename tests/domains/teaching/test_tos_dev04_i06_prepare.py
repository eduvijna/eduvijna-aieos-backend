"""TOS-DEV04-I06 PrepareTeachingWorkService happy path and failure matrix (PostgreSQL)."""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.domains.teaching.application.errors import (
    ContentMaterializationFailedError,
    EducationalQualityFailedError,
    ModelOutputInvalidError,
    ModelProviderUnavailableError,
    PreparationRecoveryInvariantError,
    WorkGenerationAlreadyExists,
    WorkGenerationInProgress,
)
from aieos.domains.teaching.application.prepare import PrepareTeachingWorkService
from aieos.platform.ai.domain.generation_run import (
    GenerationRunId,
    GenerationRunStatus,
)
from aieos.platform.ai.fake import FakeStructuredModelGateway
from aieos.platform.ai.gateway import ModelProviderUnavailable
from aieos.platform.ai.infrastructure.persistence.uow import (
    SqlAlchemyAIUnitOfWorkFactory,
)
from aieos.platform.capabilities.models import CAPABILITY_EDUCATION_GENERATE_PREPARATION_KIT
from tests.domains.content.application.test_tos_dev04_i03_atomic_preparation import (
    FailOnNthAIGenerationAuthorization,
)
from tests.domains.teaching.helpers_dev04_i06 import (
    FIXED_NOW,
    build_prepare_service,
    create_teaching_work,
    event_context,
    pass_preparation_kit,
    quality_fail_preparation_kit,
)
from tests.domains.teaching.test_tos_dev04_i02_multi_artifact_persistence import (
    _clear_i02_downgrade_blockers,
)

pytestmark = pytest.mark.tos_dev04_i06


@pytest.fixture(autouse=True)
def _cleanup_i06_shared_db_rows(postgres18: dict[str, str]) -> None:
    from sqlalchemy import create_engine

    yield
    engine = create_engine(postgres18["bootstrap_url"])
    try:
        _clear_i02_downgrade_blockers(engine)
    finally:
        engine.dispose()


def _count(bootstrap_engine: Engine, sql: str, params: dict[str, object]) -> int:
    with bootstrap_engine.connect() as conn:
        return int(conn.execute(text(sql), params).scalar_one())


def _force_running_without_finalize(
    runtime_engine: Engine,
    *,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    lease_fresh: bool = True,
) -> None:
    factory = SqlAlchemyAIUnitOfWorkFactory(runtime_engine)
    with factory(tenant_id) as uow:
        locked = uow.generation_runs.get_for_update(GenerationRunId(run_id))
        assert locked is not None
        lease = (
            locked.updated_at + timedelta(seconds=600)
            if lease_fresh
            else locked.updated_at - timedelta(seconds=30)
        )
        crashed = replace(
            locked,
            status=GenerationRunStatus.RUNNING,
            result_content_id=None,
            result_version_id=None,
            result_content_revision=None,
            completed_at=None,
            lease_expires_at=lease,
            aggregate_revision=locked.aggregate_revision + 1,
            updated_at=locked.updated_at,
        )
        assert uow.generation_runs.update(
            crashed, expected_revision=locked.aggregate_revision
        )
        uow.commit()


class TestPrepareHappyPath:
    def test_normal_prepare_six_of_six(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        service, gateway = build_prepare_service(runtime_engine)
        assert isinstance(gateway, FakeStructuredModelGateway)
        work_id, revision = create_teaching_work(
            runtime_engine, tenant_id, principal_id, idempotency_key="create-1"
        )
        result = service.prepare(
            tenant_id,
            principal_id,
            work_id=work_id,
            expected_aggregate_revision=revision,
            idempotency_key="prep-1",
            event_context=event_context(principal_id),
            now=FIXED_NOW,
        )
        assert gateway.call_count == 1
        assert len(result.artifacts) == 6
        assert tuple(a.artifact_kind for a in result.artifacts) == (
            "lesson_plan",
            "worksheet",
            "quiz",
            "homework",
            "answer_key",
            "teacher_notes",
        )
        assert result.educational_quality.status == "PASS"
        assert all(a.stewardship_state == "IN_REVIEW" for a in result.artifacts)

        with SqlAlchemyAIUnitOfWorkFactory(runtime_engine)(tenant_id) as ai_uow:
            run = ai_uow.generation_runs.get(result.generation_run_id)
            assert run is not None
            assert run.status is GenerationRunStatus.SUCCEEDED
            assert run.capability_id == CAPABILITY_EDUCATION_GENERATE_PREPARATION_KIT
            assert run.result_content_id is None
            assert run.result_version_id is None
            assert run.result_content_revision is None
            assert run.provider_response_id is not None
            assert run.educational_quality_summary is not None
            assert run.educational_quality_summary["status"] == "PASS"

        params = {"tid": tenant_id, "rid": str(result.generation_run_id.value)}
        assert _count(
            bootstrap_engine,
            "SELECT count(*) FROM content.contents WHERE tenant_id = :tid",
            params,
        ) == 6
        assert _count(
            bootstrap_engine,
            """
            SELECT count(*) FROM content.content_versions
             WHERE tenant_id = :tid AND origin = 'AI'
               AND provenance #>> '{generation_run_ref,resource_id}' = :rid
            """,
            params,
        ) == 6
        assert _count(
            bootstrap_engine,
            "SELECT count(*) FROM content.publications WHERE tenant_id = :tid",
            params,
        ) == 0
        assert _count(
            bootstrap_engine,
            "SELECT count(*) FROM content.review_decisions WHERE tenant_id = :tid",
            params,
        ) == 0
        assert _count(
            bootstrap_engine,
            "SELECT count(*) FROM ai.generation_runs WHERE tenant_id = :tid",
            {"tid": tenant_id},
        ) == 1

    def test_same_key_replay_zero_provider(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        service, gateway = build_prepare_service(runtime_engine)
        assert isinstance(gateway, FakeStructuredModelGateway)
        work_id, revision = create_teaching_work(
            runtime_engine, tenant_id, principal_id, idempotency_key="create-2"
        )
        first = service.prepare(
            tenant_id,
            principal_id,
            work_id=work_id,
            expected_aggregate_revision=revision,
            idempotency_key="prep-replay",
            event_context=event_context(principal_id),
            now=FIXED_NOW,
        )
        second = service.prepare(
            tenant_id,
            principal_id,
            work_id=work_id,
            expected_aggregate_revision=revision,
            idempotency_key="prep-replay",
            event_context=event_context(principal_id),
            now=FIXED_NOW,
        )
        assert gateway.call_count == 1
        assert second.generation_run_id == first.generation_run_id
        assert [a.content_id for a in second.artifacts] == [
            a.content_id for a in first.artifacts
        ]
        assert [a.version_id for a in second.artifacts] == [
            a.version_id for a in first.artifacts
        ]
        assert _count(
            bootstrap_engine,
            "SELECT count(*) FROM content.contents WHERE tenant_id = :tid",
            {"tid": tenant_id},
        ) == 6


class TestQualityAndModelFailures:
    def test_quality_fail_zero_content_and_replay(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        gateway = FakeStructuredModelGateway(
            result_factory=lambda _r: quality_fail_preparation_kit()
        )
        service, _ = build_prepare_service(runtime_engine, model_gateway=gateway)
        work_id, revision = create_teaching_work(
            runtime_engine, tenant_id, principal_id, idempotency_key="create-qf"
        )
        with pytest.raises(EducationalQualityFailedError) as exc_info:
            service.prepare(
                tenant_id,
                principal_id,
                work_id=work_id,
                expected_aggregate_revision=revision,
                idempotency_key="prep-qf",
                event_context=event_context(principal_id),
                now=FIXED_NOW,
            )
        assert gateway.call_count == 1
        assert exc_info.value.educational_quality is not None
        assert _count(
            bootstrap_engine,
            "SELECT count(*) FROM content.contents WHERE tenant_id = :tid",
            {"tid": tenant_id},
        ) == 0

        with pytest.raises(EducationalQualityFailedError):
            service.prepare(
                tenant_id,
                principal_id,
                work_id=work_id,
                expected_aggregate_revision=revision,
                idempotency_key="prep-qf",
                event_context=event_context(principal_id),
                now=FIXED_NOW,
            )
        assert gateway.call_count == 1

        # New key may retry
        gateway2 = FakeStructuredModelGateway(
            result_factory=lambda _r: pass_preparation_kit()
        )
        service2, _ = build_prepare_service(runtime_engine, model_gateway=gateway2)
        ok = service2.prepare(
            tenant_id,
            principal_id,
            work_id=work_id,
            expected_aggregate_revision=revision,
            idempotency_key="prep-qf-new",
            event_context=event_context(principal_id),
            now=FIXED_NOW,
        )
        assert len(ok.artifacts) == 6
        assert gateway2.call_count == 1

    def test_provider_unavailable(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        gateway = FakeStructuredModelGateway(error=ModelProviderUnavailable("down"))
        service, _ = build_prepare_service(runtime_engine, model_gateway=gateway)
        work_id, revision = create_teaching_work(
            runtime_engine, tenant_id, principal_id, idempotency_key="create-unavail"
        )
        with pytest.raises(ModelProviderUnavailableError):
            service.prepare(
                tenant_id,
                principal_id,
                work_id=work_id,
                expected_aggregate_revision=revision,
                idempotency_key="prep-unavail",
                event_context=event_context(principal_id),
                now=FIXED_NOW,
            )
        assert gateway.call_count == 1
        assert _count(
            bootstrap_engine,
            "SELECT count(*) FROM content.contents WHERE tenant_id = :tid",
            {"tid": tenant_id},
        ) == 0
        with pytest.raises(ModelProviderUnavailableError):
            service.prepare(
                tenant_id,
                principal_id,
                work_id=work_id,
                expected_aggregate_revision=revision,
                idempotency_key="prep-unavail",
                event_context=event_context(principal_id),
                now=FIXED_NOW,
            )
        assert gateway.call_count == 1

    def test_content_rollback_failure(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        authz = FailOnNthAIGenerationAuthorization(fail_on_call=4)
        service, gateway = build_prepare_service(runtime_engine, authz=authz)
        assert isinstance(gateway, FakeStructuredModelGateway)
        work_id, revision = create_teaching_work(
            runtime_engine, tenant_id, principal_id, idempotency_key="create-rb"
        )
        with pytest.raises(ContentMaterializationFailedError):
            service.prepare(
                tenant_id,
                principal_id,
                work_id=work_id,
                expected_aggregate_revision=revision,
                idempotency_key="prep-rb",
                event_context=event_context(principal_id),
                now=FIXED_NOW,
            )
        assert gateway.call_count == 1
        assert _count(
            bootstrap_engine,
            "SELECT count(*) FROM content.contents WHERE tenant_id = :tid",
            {"tid": tenant_id},
        ) == 0
        with pytest.raises(ContentMaterializationFailedError):
            service.prepare(
                tenant_id,
                principal_id,
                work_id=work_id,
                expected_aggregate_revision=revision,
                idempotency_key="prep-rb",
                event_context=event_context(principal_id),
                now=FIXED_NOW,
            )
        assert gateway.call_count == 1


class TestRecoveryAndFences:
    def test_lost_response_same_key_content_first(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        service, gateway = build_prepare_service(runtime_engine)
        assert isinstance(gateway, FakeStructuredModelGateway)
        work_id, revision = create_teaching_work(
            runtime_engine, tenant_id, principal_id, idempotency_key="create-lost"
        )
        first = service.prepare(
            tenant_id,
            principal_id,
            work_id=work_id,
            expected_aggregate_revision=revision,
            idempotency_key="prep-lost",
            event_context=event_context(principal_id),
            now=FIXED_NOW,
        )
        _force_running_without_finalize(
            runtime_engine,
            tenant_id=tenant_id,
            run_id=first.generation_run_id.value,
            lease_fresh=True,
        )
        second = service.prepare(
            tenant_id,
            principal_id,
            work_id=work_id,
            expected_aggregate_revision=revision,
            idempotency_key="prep-lost",
            event_context=event_context(principal_id),
            now=FIXED_NOW,
        )
        assert gateway.call_count == 1
        assert second.generation_run_id == first.generation_run_id
        assert [a.content_id for a in second.artifacts] == [
            a.content_id for a in first.artifacts
        ]
        with SqlAlchemyAIUnitOfWorkFactory(runtime_engine)(tenant_id) as ai_uow:
            run = ai_uow.generation_runs.get(first.generation_run_id)
            assert run is not None
            assert run.status is GenerationRunStatus.SUCCEEDED
        assert _count(
            bootstrap_engine,
            "SELECT count(*) FROM content.contents WHERE tenant_id = :tid",
            {"tid": tenant_id},
        ) == 6

    def test_different_key_after_committed_before_finalize(
        self, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        service, gateway = build_prepare_service(runtime_engine)
        assert isinstance(gateway, FakeStructuredModelGateway)
        work_id, revision = create_teaching_work(
            runtime_engine, tenant_id, principal_id, idempotency_key="create-diff"
        )
        first = service.prepare(
            tenant_id,
            principal_id,
            work_id=work_id,
            expected_aggregate_revision=revision,
            idempotency_key="prep-old",
            event_context=event_context(principal_id),
            now=FIXED_NOW,
        )
        _force_running_without_finalize(
            runtime_engine,
            tenant_id=tenant_id,
            run_id=first.generation_run_id.value,
            lease_fresh=True,
        )
        with pytest.raises(WorkGenerationAlreadyExists) as exc_info:
            service.prepare(
                tenant_id,
                principal_id,
                work_id=work_id,
                expected_aggregate_revision=revision,
                idempotency_key="prep-new",
                event_context=event_context(principal_id),
                now=FIXED_NOW,
            )
        assert gateway.call_count == 1
        assert exc_info.value.existing_generation_run_id == first.generation_run_id
        assert exc_info.value.existing_content_id is None
        assert exc_info.value.existing_version_id is None

    def test_stale_same_key_zero_content_reclaim(
        self, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        call_count = {"n": 0}

        def factory(_req):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise ModelProviderUnavailable("crash before content")
            return pass_preparation_kit()

        gateway = FakeStructuredModelGateway(result_factory=factory)
        service, _ = build_prepare_service(runtime_engine, model_gateway=gateway)
        work_id, revision = create_teaching_work(
            runtime_engine, tenant_id, principal_id, idempotency_key="create-stale"
        )
        with pytest.raises(ModelProviderUnavailableError):
            service.prepare(
                tenant_id,
                principal_id,
                work_id=work_id,
                expected_aggregate_revision=revision,
                idempotency_key="prep-stale-same",
                event_context=event_context(principal_id),
                now=FIXED_NOW,
            )
        # Force FAILED back to stale RUNNING with zero content for reclaim path
        with SqlAlchemyAIUnitOfWorkFactory(runtime_engine)(tenant_id) as ai_uow:
            from aieos.platform.idempotency.hashing import hash_idempotency_key

            run = ai_uow.generation_runs.get_by_idempotency_key(
                principal_id=principal_id,
                idempotency_key_sha256=hash_idempotency_key("prep-stale-same"),
            )
            assert run is not None
            locked = ai_uow.generation_runs.get_for_update(run.generation_run_id)
            assert locked is not None
            stale = replace(
                locked,
                status=GenerationRunStatus.RUNNING,
                failure_code=None,
                completed_at=None,
                lease_expires_at=locked.updated_at - timedelta(seconds=30),
                educational_quality_summary=None,
                provider_response_id=None,
                aggregate_revision=locked.aggregate_revision + 1,
            )
            assert ai_uow.generation_runs.update(
                stale, expected_revision=locked.aggregate_revision
            )
            ai_uow.commit()
            run_id = run.generation_run_id

        result = service.prepare(
            tenant_id,
            principal_id,
            work_id=work_id,
            expected_aggregate_revision=revision,
            idempotency_key="prep-stale-same",
            event_context=event_context(principal_id),
            now=FIXED_NOW,
        )
        assert result.generation_run_id == run_id
        assert call_count["n"] == 2
        assert len(result.artifacts) == 6

    def test_stale_different_key_zero_content(
        self, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        gateway = FakeStructuredModelGateway(
            result_factory=lambda _r: pass_preparation_kit()
        )
        service, _ = build_prepare_service(runtime_engine, model_gateway=gateway)
        work_id, revision = create_teaching_work(
            runtime_engine, tenant_id, principal_id, idempotency_key="create-stale2"
        )
        # Seed stale RUNNING with zero content under old key
        with SqlAlchemyAIUnitOfWorkFactory(runtime_engine)(tenant_id) as ai_uow:
            from aieos.platform.ai.domain.generation_run import GenerationRun
            from aieos.platform.idempotency.hashing import (
                fingerprint_material,
                hash_idempotency_key,
            )

            fingerprint = fingerprint_material(
                {
                    "work_id": str(work_id),
                    "work_revision": int(revision),
                    "capability_id": CAPABILITY_EDUCATION_GENERATE_PREPARATION_KIT,
                    "provider_id": "fake",
                    "model_id": "fake-model",
                }
            )
            old = GenerationRun(
                generation_run_id=GenerationRunId.generate(),
                tenant_id=tenant_id,
                principal_id=principal_id,
                work_resource_type="teaching.work",
                work_resource_id=work_id.value,
                work_resource_revision=int(revision),
                capability_id=CAPABILITY_EDUCATION_GENERATE_PREPARATION_KIT,
                provider_id="fake",
                model_id="fake-model",
                status=GenerationRunStatus.RUNNING,
                request_fingerprint_sha256=fingerprint,
                idempotency_key_sha256=hash_idempotency_key("old-key"),
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
                created_at=FIXED_NOW,
                updated_at=FIXED_NOW,
                completed_at=None,
                lease_expires_at=FIXED_NOW - timedelta(seconds=30),
            )
            ai_uow.generation_runs.insert(old)
            ai_uow.commit()
            old_id = old.generation_run_id

        result = service.prepare(
            tenant_id,
            principal_id,
            work_id=work_id,
            expected_aggregate_revision=revision,
            idempotency_key="new-key",
            event_context=event_context(principal_id),
            now=FIXED_NOW,
        )
        assert result.generation_run_id != old_id
        assert gateway.call_count == 1
        with SqlAlchemyAIUnitOfWorkFactory(runtime_engine)(tenant_id) as ai_uow:
            stale = ai_uow.generation_runs.get(old_id)
            assert stale is not None
            assert stale.status is GenerationRunStatus.FAILED
            assert stale.failure_code == "generation_lease_expired"
            new = ai_uow.generation_runs.get(result.generation_run_id)
            assert new is not None
            assert new.status is GenerationRunStatus.SUCCEEDED

    def test_provider_model_cannot_bypass_fence_a(
        self, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        service1, gateway1 = build_prepare_service(
            runtime_engine, provider_id="fake-a", model_id="model-a"
        )
        work_id, revision = create_teaching_work(
            runtime_engine, tenant_id, principal_id, idempotency_key="create-fence"
        )
        first = service1.prepare(
            tenant_id,
            principal_id,
            work_id=work_id,
            expected_aggregate_revision=revision,
            idempotency_key="prep-a",
            event_context=event_context(principal_id),
            now=FIXED_NOW,
        )
        assert isinstance(gateway1, FakeStructuredModelGateway)
        assert gateway1.call_count == 1
        service2, gateway2 = build_prepare_service(
            runtime_engine, provider_id="fake-b", model_id="model-b"
        )
        assert isinstance(gateway2, FakeStructuredModelGateway)
        with pytest.raises(WorkGenerationAlreadyExists):
            service2.prepare(
                tenant_id,
                principal_id,
                work_id=work_id,
                expected_aggregate_revision=revision,
                idempotency_key="prep-b",
                event_context=event_context(principal_id),
                now=FIXED_NOW,
            )
        assert gateway2.call_count == 0
        assert first.generation_run_id is not None

    def test_partial_bindings_fail_closed(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        service, gateway = build_prepare_service(runtime_engine)
        assert isinstance(gateway, FakeStructuredModelGateway)
        work_id, revision = create_teaching_work(
            runtime_engine, tenant_id, principal_id, idempotency_key="create-partial"
        )
        first = service.prepare(
            tenant_id,
            principal_id,
            work_id=work_id,
            expected_aggregate_revision=revision,
            idempotency_key="prep-partial",
            event_context=event_context(principal_id),
            now=FIXED_NOW,
        )
        _force_running_without_finalize(
            runtime_engine,
            tenant_id=tenant_id,
            run_id=first.generation_run_id.value,
            lease_fresh=False,
        )
        with bootstrap_engine.begin() as conn:
            conn.execute(text("SET LOCAL session_replication_role = replica"))
            conn.execute(
                text(
                    """
                    DELETE FROM content.content_versions
                     WHERE tenant_id = :tid
                       AND provenance #>> '{generation_run_ref,resource_id}' = :rid
                       AND (provenance->>'artifact_kind') IN
                           ('quiz', 'homework', 'answer_key')
                    """
                ),
                {"tid": tenant_id, "rid": str(first.generation_run_id.value)},
            )

        with pytest.raises(PreparationRecoveryInvariantError):
            service.prepare(
                tenant_id,
                principal_id,
                work_id=work_id,
                expected_aggregate_revision=revision,
                idempotency_key="prep-partial-new",
                event_context=event_context(principal_id),
                now=FIXED_NOW,
            )
        assert gateway.call_count == 1
        with SqlAlchemyAIUnitOfWorkFactory(runtime_engine)(tenant_id) as ai_uow:
            run = ai_uow.generation_runs.get(first.generation_run_id)
            assert run is not None
            assert run.status is not GenerationRunStatus.SUCCEEDED

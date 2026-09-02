"""TOS-DEV04-I06 cross-revision recovery and architecture guards."""

from __future__ import annotations

import ast
import uuid
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.domains.teaching.application.errors import WorkGenerationInProgress
from aieos.domains.teaching.application.refine import RefineTeachingWorkService
from aieos.domains.teaching.application.models import RefineTeachingWorkCommand
from aieos.domains.teaching.domain.identities import AggregateRevision
from aieos.domains.teaching.infrastructure.persistence.uow import (
    SqlAlchemyTeachingUnitOfWorkFactory,
)
from aieos.platform.ai.domain.generation_run import GenerationRunId, GenerationRunStatus
from aieos.platform.ai.fake import FakeStructuredModelGateway
from aieos.platform.ai.infrastructure.persistence.uow import (
    SqlAlchemyAIUnitOfWorkFactory,
)
from aieos.platform.idempotency.hashing import hash_idempotency_key
from tests.dbutil import REPO_ROOT
from tests.domains.teaching.helpers_dev04_i06 import (
    FIXED_NOW,
    build_prepare_service,
    create_teaching_work,
    event_context,
    pass_preparation_kit,
)
from tests.domains.teaching.test_tos_dev04_i02_multi_artifact_persistence import (
    _clear_i02_downgrade_blockers,
)
from tests.fakes import IDEMPOTENCY_RETENTION

pytestmark = pytest.mark.tos_dev04_i06


@pytest.fixture(autouse=True)
def _cleanup_i06_cross_rev(postgres18: dict[str, str]) -> None:
    from sqlalchemy import create_engine

    yield
    engine = create_engine(postgres18["bootstrap_url"])
    try:
        _clear_i02_downgrade_blockers(engine)
    finally:
        engine.dispose()


def _refine_to_r1(
    runtime_engine: Engine,
    tenant_id: uuid.UUID,
    principal_id: uuid.UUID,
    work_id,
    revision: AggregateRevision,
) -> AggregateRevision:
    service = RefineTeachingWorkService(
        SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine),
        idempotency_retention=IDEMPOTENCY_RETENTION,
    )
    refined = service.refine(
        tenant_id,
        principal_id,
        work_id=work_id,
        expected_aggregate_revision=revision,
        command=RefineTeachingWorkCommand(
            goal_text="Refined goal for revision R1 with clearer fraction focus."
        ),
        idempotency_key="refine-r1",
        now=FIXED_NOW,
    )
    return refined.aggregate_revision


class TestCrossRevision:
    def test_r0_fresh_running_blocks_r1(self, runtime_engine: Engine) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        call = {"n": 0}

        def factory(_req):
            call["n"] += 1
            if call["n"] == 1:
                # Leave a RUNNING holder by raising after claim... better: prepare
                # then force RUNNING with zero content and fresh lease.
                return pass_preparation_kit()
            return pass_preparation_kit()

        gateway = FakeStructuredModelGateway(result_factory=factory)
        service, _ = build_prepare_service(runtime_engine, model_gateway=gateway)
        work_id, r0 = create_teaching_work(
            runtime_engine, tenant_id, principal_id, idempotency_key="c-xr1"
        )
        # Seed fresh RUNNING zero-content for R0
        from aieos.platform.ai.domain.generation_run import GenerationRun
        from aieos.platform.idempotency.hashing import fingerprint_material
        from aieos.platform.capabilities.models import (
            CAPABILITY_EDUCATION_GENERATE_PREPARATION_KIT,
        )

        with SqlAlchemyAIUnitOfWorkFactory(runtime_engine)(tenant_id) as ai_uow:
            fingerprint = fingerprint_material(
                {
                    "work_id": str(work_id),
                    "work_revision": int(r0),
                    "capability_id": CAPABILITY_EDUCATION_GENERATE_PREPARATION_KIT,
                    "provider_id": "fake",
                    "model_id": "fake-model",
                }
            )
            run = GenerationRun(
                generation_run_id=GenerationRunId.generate(),
                tenant_id=tenant_id,
                principal_id=principal_id,
                work_resource_type="teaching.work",
                work_resource_id=work_id.value,
                work_resource_revision=int(r0),
                capability_id=CAPABILITY_EDUCATION_GENERATE_PREPARATION_KIT,
                provider_id="fake",
                model_id="fake-model",
                status=GenerationRunStatus.RUNNING,
                request_fingerprint_sha256=fingerprint,
                idempotency_key_sha256=hash_idempotency_key("r0-holder"),
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
                lease_expires_at=FIXED_NOW + timedelta(seconds=600),
            )
            ai_uow.generation_runs.insert(run)
            ai_uow.commit()

        r1 = _refine_to_r1(runtime_engine, tenant_id, principal_id, work_id, r0)
        with pytest.raises(WorkGenerationInProgress):
            service.prepare(
                tenant_id,
                principal_id,
                work_id=work_id,
                expected_aggregate_revision=r1,
                idempotency_key="r1-key",
                event_context=event_context(principal_id),
                now=FIXED_NOW,
            )
        assert gateway.call_count == 0

    def test_r0_stale_zero_allows_r1_claim(self, runtime_engine: Engine) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        gateway = FakeStructuredModelGateway(
            result_factory=lambda _r: pass_preparation_kit()
        )
        service, _ = build_prepare_service(runtime_engine, model_gateway=gateway)
        work_id, r0 = create_teaching_work(
            runtime_engine, tenant_id, principal_id, idempotency_key="c-xr2"
        )
        from aieos.platform.ai.domain.generation_run import GenerationRun
        from aieos.platform.idempotency.hashing import fingerprint_material
        from aieos.platform.capabilities.models import (
            CAPABILITY_EDUCATION_GENERATE_PREPARATION_KIT,
        )

        with SqlAlchemyAIUnitOfWorkFactory(runtime_engine)(tenant_id) as ai_uow:
            fingerprint = fingerprint_material(
                {
                    "work_id": str(work_id),
                    "work_revision": int(r0),
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
                work_resource_revision=int(r0),
                capability_id=CAPABILITY_EDUCATION_GENERATE_PREPARATION_KIT,
                provider_id="fake",
                model_id="fake-model",
                status=GenerationRunStatus.RUNNING,
                request_fingerprint_sha256=fingerprint,
                idempotency_key_sha256=hash_idempotency_key("r0-stale"),
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

        r1 = _refine_to_r1(runtime_engine, tenant_id, principal_id, work_id, r0)
        result = service.prepare(
            tenant_id,
            principal_id,
            work_id=work_id,
            expected_aggregate_revision=r1,
            idempotency_key="r1-claim",
            event_context=event_context(principal_id),
            now=FIXED_NOW,
        )
        assert gateway.call_count == 1
        assert result.work_revision == int(r1)
        assert result.generation_run_id != old_id
        with SqlAlchemyAIUnitOfWorkFactory(runtime_engine)(tenant_id) as ai_uow:
            stale = ai_uow.generation_runs.get(old_id)
            assert stale is not None
            assert stale.status is GenerationRunStatus.FAILED
            assert stale.work_resource_revision == int(r0)

    def test_r0_committed_reconciled_does_not_satisfy_r1(
        self, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        service, gateway = build_prepare_service(runtime_engine)
        assert isinstance(gateway, FakeStructuredModelGateway)
        work_id, r0 = create_teaching_work(
            runtime_engine, tenant_id, principal_id, idempotency_key="c-xr3"
        )
        r0_result = service.prepare(
            tenant_id,
            principal_id,
            work_id=work_id,
            expected_aggregate_revision=r0,
            idempotency_key="prep-r0",
            event_context=event_context(principal_id),
            now=FIXED_NOW,
        )
        # Crash finalization to RUNNING with content still present
        with SqlAlchemyAIUnitOfWorkFactory(runtime_engine)(tenant_id) as ai_uow:
            locked = ai_uow.generation_runs.get_for_update(r0_result.generation_run_id)
            assert locked is not None
            crashed = replace(
                locked,
                status=GenerationRunStatus.RUNNING,
                result_content_id=None,
                result_version_id=None,
                result_content_revision=None,
                completed_at=None,
                lease_expires_at=locked.updated_at + timedelta(seconds=600),
                aggregate_revision=locked.aggregate_revision + 1,
            )
            assert ai_uow.generation_runs.update(
                crashed, expected_revision=locked.aggregate_revision
            )
            ai_uow.commit()

        r1 = _refine_to_r1(runtime_engine, tenant_id, principal_id, work_id, r0)
        r1_result = service.prepare(
            tenant_id,
            principal_id,
            work_id=work_id,
            expected_aggregate_revision=r1,
            idempotency_key="prep-r1",
            event_context=event_context(principal_id),
            now=FIXED_NOW,
        )
        assert gateway.call_count == 2
        assert r1_result.generation_run_id != r0_result.generation_run_id
        assert r1_result.work_revision == int(r1)
        r0_ids = {a.content_id for a in r0_result.artifacts}
        r1_ids = {a.content_id for a in r1_result.artifacts}
        assert r0_ids.isdisjoint(r1_ids)
        with SqlAlchemyAIUnitOfWorkFactory(runtime_engine)(tenant_id) as ai_uow:
            r0_run = ai_uow.generation_runs.get(r0_result.generation_run_id)
            assert r0_run is not None
            assert r0_run.status is GenerationRunStatus.SUCCEEDED
            assert r0_run.work_resource_revision == int(r0)


class TestArchitecture:
    def test_no_migration_and_prepare_is_http_composed(self) -> None:
        versions = sorted((REPO_ROOT / "migrations" / "versions").glob("*.py"))
        assert versions[-1].name.startswith("tosd070001_")
        assert not any(p.name.startswith("tosd040002_") for p in versions)
        # I07 composes PrepareTeachingWorkService into create_app / routes.
        factory = (
            REPO_ROOT / "src" / "aieos" / "platform" / "api" / "app.py"
        ).read_text(encoding="utf-8")
        assert "PrepareTeachingWorkService" in factory
        routes = (
            REPO_ROOT / "src" / "aieos" / "domains" / "teaching" / "api" / "v1" / "routes.py"
        ).read_text(encoding="utf-8")
        assert "actions/prepare" in routes
        assert 'operation_id="teaching_work_prepare"' in routes

    def test_prepare_module_has_no_forbidden_imports(self) -> None:
        path = (
            REPO_ROOT
            / "src"
            / "aieos"
            / "domains"
            / "teaching"
            / "application"
            / "prepare.py"
        )
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".")[0])
        for forbidden in (
            "openai",
            "fastapi",
            "temporalio",
            "langchain",
            "mcp",
        ):
            assert forbidden not in imports
        source = path.read_text(encoding="utf-8").lower()
        assert "openai" not in source
        # Application service remains HTTP-free; route owns actions/prepare.
        assert "actions/prepare" not in source


class TestExactSixRecoveryHardening:
    def _prepare_exact_six(
        self, runtime_engine: Engine, tenant_id: uuid.UUID, principal_id: uuid.UUID
    ):
        service, gateway = build_prepare_service(runtime_engine)
        assert isinstance(gateway, FakeStructuredModelGateway)
        work_id, revision = create_teaching_work(
            runtime_engine, tenant_id, principal_id, idempotency_key="rec-harden-create"
        )
        result = service.prepare(
            tenant_id,
            principal_id,
            work_id=work_id,
            expected_aggregate_revision=revision,
            idempotency_key="rec-harden-prep",
            event_context=event_context(principal_id),
            now=FIXED_NOW,
        )
        return result

    def _inspect(self, runtime_engine: Engine, tenant_id: uuid.UUID, run_id):
        from aieos.domains.content.application.preparation_recovery import (
            inspect_preparation_generation_bindings,
        )
        from aieos.domains.content.infrastructure.persistence.uow import (
            SqlAlchemyContentUnitOfWorkFactory,
        )

        with SqlAlchemyAIUnitOfWorkFactory(runtime_engine)(tenant_id) as ai_uow:
            run = ai_uow.generation_runs.get(run_id)
            assert run is not None
        with SqlAlchemyContentUnitOfWorkFactory(runtime_engine)(tenant_id) as cuow:
            return inspect_preparation_generation_bindings(cuow, run)

    def test_mismatched_generation_run_ref_revision_is_invalid(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        from aieos.domains.content.application.preparation_recovery import (
            PreparationBindingRecoveryStatus,
        )

        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        result = self._prepare_exact_six(runtime_engine, tenant_id, principal_id)
        with bootstrap_engine.begin() as conn:
            conn.execute(text("SET LOCAL session_replication_role = replica"))
            conn.execute(
                text(
                    """
                    UPDATE content.content_versions
                       SET provenance = jsonb_set(
                         provenance,
                         '{generation_run_ref,resource_revision}',
                         '999'::jsonb,
                         true
                       )
                     WHERE tenant_id = :tid
                       AND provenance #>> '{generation_run_ref,resource_id}' = :rid
                       AND (provenance->>'artifact_kind') = 'quiz'
                    """
                ),
                {"tid": tenant_id, "rid": str(result.generation_run_id.value)},
            )
        inspection = self._inspect(
            runtime_engine, tenant_id, result.generation_run_id
        )
        assert inspection.status is PreparationBindingRecoveryStatus.INVALID
        assert inspection.detail is not None

    def test_wrong_generation_run_ref_type_is_invalid(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        from aieos.domains.content.application.preparation_recovery import (
            PreparationBindingRecoveryStatus,
        )

        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        result = self._prepare_exact_six(runtime_engine, tenant_id, principal_id)
        with bootstrap_engine.begin() as conn:
            conn.execute(text("SET LOCAL session_replication_role = replica"))
            conn.execute(
                text(
                    """
                    UPDATE content.content_versions
                       SET provenance = jsonb_set(
                         provenance,
                         '{generation_run_ref,resource_type}',
                         '"ai.wrong_type"'::jsonb,
                         true
                       )
                     WHERE tenant_id = :tid
                       AND provenance #>> '{generation_run_ref,resource_id}' = :rid
                       AND (provenance->>'artifact_kind') = 'homework'
                    """
                ),
                {"tid": tenant_id, "rid": str(result.generation_run_id.value)},
            )
        inspection = self._inspect(
            runtime_engine, tenant_id, result.generation_run_id
        )
        assert inspection.status is PreparationBindingRecoveryStatus.INVALID

    def test_null_generation_run_ref_revision_is_invalid(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        from aieos.domains.content.application.preparation_recovery import (
            PreparationBindingRecoveryStatus,
        )

        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        result = self._prepare_exact_six(runtime_engine, tenant_id, principal_id)
        with bootstrap_engine.begin() as conn:
            conn.execute(text("SET LOCAL session_replication_role = replica"))
            conn.execute(
                text(
                    """
                    UPDATE content.content_versions
                       SET provenance = jsonb_set(
                         provenance,
                         '{generation_run_ref,resource_revision}',
                         'null'::jsonb,
                         true
                       )
                     WHERE tenant_id = :tid
                       AND provenance #>> '{generation_run_ref,resource_id}' = :rid
                       AND (provenance->>'artifact_kind') = 'worksheet'
                    """
                ),
                {"tid": tenant_id, "rid": str(result.generation_run_id.value)},
            )
        inspection = self._inspect(
            runtime_engine, tenant_id, result.generation_run_id
        )
        assert inspection.status is PreparationBindingRecoveryStatus.INVALID

    def test_provider_id_mismatch_vs_generation_run_is_invalid(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        from aieos.domains.content.application.preparation_recovery import (
            PreparationBindingRecoveryStatus,
        )

        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        result = self._prepare_exact_six(runtime_engine, tenant_id, principal_id)
        with bootstrap_engine.begin() as conn:
            conn.execute(text("SET LOCAL session_replication_role = replica"))
            conn.execute(
                text(
                    """
                    UPDATE content.content_versions
                       SET provenance = jsonb_set(
                         provenance,
                         '{provider_id}',
                         '"other-provider"'::jsonb,
                         true
                       )
                     WHERE tenant_id = :tid
                       AND provenance #>> '{generation_run_ref,resource_id}' = :rid
                    """
                ),
                {"tid": tenant_id, "rid": str(result.generation_run_id.value)},
            )
        inspection = self._inspect(
            runtime_engine, tenant_id, result.generation_run_id
        )
        assert inspection.status is PreparationBindingRecoveryStatus.INVALID

    def test_model_id_mismatch_vs_generation_run_is_invalid(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        from aieos.domains.content.application.preparation_recovery import (
            PreparationBindingRecoveryStatus,
        )

        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        result = self._prepare_exact_six(runtime_engine, tenant_id, principal_id)
        with bootstrap_engine.begin() as conn:
            conn.execute(text("SET LOCAL session_replication_role = replica"))
            conn.execute(
                text(
                    """
                    UPDATE content.content_versions
                       SET provenance = jsonb_set(
                         provenance,
                         '{model_id}',
                         '"other-model"'::jsonb,
                         true
                       )
                     WHERE tenant_id = :tid
                       AND provenance #>> '{generation_run_ref,resource_id}' = :rid
                    """
                ),
                {"tid": tenant_id, "rid": str(result.generation_run_id.value)},
            )
        inspection = self._inspect(
            runtime_engine, tenant_id, result.generation_run_id
        )
        assert inspection.status is PreparationBindingRecoveryStatus.INVALID

    def test_content_version_tenant_mismatch_is_invalid(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        from aieos.domains.content.application.preparation_recovery import (
            PreparationBindingRecoveryStatus,
        )

        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        other_tenant = uuid.uuid7()
        result = self._prepare_exact_six(runtime_engine, tenant_id, principal_id)
        with bootstrap_engine.begin() as conn:
            conn.execute(text("SET LOCAL session_replication_role = replica"))
            conn.execute(
                text(
                    """
                    UPDATE content.content_versions
                       SET tenant_id = :other
                     WHERE tenant_id = :tid
                       AND provenance #>> '{generation_run_ref,resource_id}' = :rid
                       AND (provenance->>'artifact_kind') = 'teacher_notes'
                    """
                ),
                {
                    "tid": tenant_id,
                    "rid": str(result.generation_run_id.value),
                    "other": other_tenant,
                },
            )
        inspection = self._inspect(
            runtime_engine, tenant_id, result.generation_run_id
        )
        # find_all_by_generation_run may be tenant-scoped; if the row is invisible
        # under the run tenant the binding set becomes incomplete → INVALID.
        assert inspection.status is PreparationBindingRecoveryStatus.INVALID

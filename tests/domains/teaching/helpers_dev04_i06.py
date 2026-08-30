"""Shared TOS-DEV04-I06 PrepareTeachingWorkService construction helpers."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from sqlalchemy.engine import Engine

from aieos.domains.content.application.ai_preparation_for_review import (
    CreateAIPreparationArtifactsForReviewService,
)
from aieos.domains.content.application.catalog import StaticContentTypeCatalog
from aieos.domains.content.infrastructure.persistence.uow import (
    SqlAlchemyContentUnitOfWorkFactory,
)
from aieos.domains.education.application.generate_preparation_kit import (
    GeneratePreparationKitCapability,
)
from aieos.domains.education.preparation_kit_v1 import PreparationKitV1
from aieos.domains.education.schema import (
    PREPARATION_CONTENT_TYPES,
    build_preparation_content_schema_registry,
)
from aieos.domains.teaching.application.create import CreateTeachingWorkService
from aieos.domains.teaching.application.models import CreateTeachingWorkCommand
from aieos.domains.teaching.application.prepare import PrepareTeachingWorkService
from aieos.domains.teaching.domain.identities import AggregateRevision, WorkId
from aieos.domains.teaching.infrastructure.persistence.uow import (
    SqlAlchemyTeachingUnitOfWorkFactory,
)
from aieos.platform.ai.clock import UtcNow
from aieos.platform.ai.fake import FakeStructuredModelGateway
from aieos.platform.ai.infrastructure.persistence.uow import (
    SqlAlchemyAIUnitOfWorkFactory,
)
from aieos.platform.events.models import MutationEventContext
from aieos.platform.ai.gateway import StructuredModelGateway
from tests.fakes import (
    IDEMPOTENCY_RETENTION,
    AllowAIGenerationAuthorization,
    AllowAssetReferenceValidation,
)
from tests.platform.education.test_tos_dev04_i05_preparation_quality import (
    _pass_kit_payload,
)

FIXED_NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)


def pass_preparation_kit() -> PreparationKitV1:
    return PreparationKitV1.model_validate(_pass_kit_payload())


def quality_fail_preparation_kit() -> PreparationKitV1:
    """Structurally buildable kit that fails I05 (single bloom band)."""
    payload = _pass_kit_payload()
    for question in payload["worksheet"]["questions"]:  # type: ignore[index]
        question["bloom_level"] = "remember"
    return PreparationKitV1.model_validate(payload)


def build_prepare_service(
    runtime_engine: Engine,
    *,
    model_gateway: StructuredModelGateway | None = None,
    provider_id: str = "fake",
    model_id: str = "fake-model",
    lease_seconds: int = 120,
    clock: UtcNow | None = None,
    authz=None,
) -> tuple[PrepareTeachingWorkService, FakeStructuredModelGateway | StructuredModelGateway]:
    gateway: StructuredModelGateway
    if model_gateway is None:
        gateway = FakeStructuredModelGateway(
            result_factory=lambda _req: pass_preparation_kit()
        )
    else:
        gateway = model_gateway
    capability = GeneratePreparationKitCapability(gateway)
    materializer = CreateAIPreparationArtifactsForReviewService(
        SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
        StaticContentTypeCatalog(set(PREPARATION_CONTENT_TYPES) | {"test.generic"}),
        build_preparation_content_schema_registry(),
        AllowAssetReferenceValidation(),
        authz if authz is not None else AllowAIGenerationAuthorization(),
    )
    service = PrepareTeachingWorkService(
        SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine),
        SqlAlchemyAIUnitOfWorkFactory(runtime_engine),
        SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
        capability,
        materializer,
        provider_id=provider_id,
        model_id=model_id,
        lease_seconds=lease_seconds,
        clock=clock if clock is not None else (lambda: FIXED_NOW),
    )
    return service, gateway


def create_teaching_work(
    runtime_engine: Engine,
    tenant_id: UUID,
    principal_id: UUID,
    *,
    idempotency_key: str,
    goal_text: str = (
        "Tomorrow my Grade 5 students need to understand fractions "
        "using visual examples."
    ),
) -> tuple[WorkId, AggregateRevision]:
    service = CreateTeachingWorkService(
        SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine),
        idempotency_retention=IDEMPOTENCY_RETENTION,
    )
    created = service.create(
        tenant_id,
        principal_id,
        CreateTeachingWorkCommand(
            intent_type="prepare_tomorrow",
            goal_text=goal_text,
            target_date=date(2026, 9, 1),
            locale="en-IN",
            class_label="Grade 5-A",
            subject="Mathematics",
            topic="Fractions",
        ),
        idempotency_key=idempotency_key,
        now=FIXED_NOW,
    )
    return created.work_id, created.aggregate_revision


def event_context(principal_id: UUID, correlation_id: UUID | None = None) -> MutationEventContext:
    from uuid import uuid7

    cid = correlation_id if correlation_id is not None else uuid7()
    return MutationEventContext(
        correlation_id=cid,
        causation_id=uuid7(),
        actor_principal_id=principal_id,
        effective_actor_id=principal_id,
    )


def advance_running_claim_revision(
    runtime_engine: Engine,
    *,
    tenant_id: UUID,
    principal_id: UUID,
    idempotency_key: str,
) -> int:
    """Simulate a mid-provider same-key reclaim (newer RUNNING aggregate revision)."""
    from dataclasses import replace

    from aieos.platform.ai.domain.generation_run import GenerationRunStatus
    from aieos.platform.idempotency.hashing import hash_idempotency_key

    with SqlAlchemyAIUnitOfWorkFactory(runtime_engine)(tenant_id) as ai_uow:
        run = ai_uow.generation_runs.get_by_idempotency_key(
            principal_id=principal_id,
            idempotency_key_sha256=hash_idempotency_key(idempotency_key),
        )
        assert run is not None
        locked = ai_uow.generation_runs.get_for_update(run.generation_run_id)
        assert locked is not None
        assert locked.status is GenerationRunStatus.RUNNING
        advanced = replace(
            locked,
            status=GenerationRunStatus.RUNNING,
            lease_expires_at=FIXED_NOW + timedelta(seconds=600),
            updated_at=FIXED_NOW,
            aggregate_revision=locked.aggregate_revision + 1,
            # Distinguish newer claimant metadata from the stale provider draft.
            provider_response_id="newer-claimant-response",
        )
        assert ai_uow.generation_runs.update(
            advanced, expected_revision=locked.aggregate_revision
        )
        ai_uow.commit()
        return advanced.aggregate_revision


def terminalize_running_as_lease_expired(
    runtime_engine: Engine,
    *,
    tenant_id: UUID,
    principal_id: UUID,
    idempotency_key: str,
) -> None:
    """Simulate different-key stale resolver terminalizing the claimed run mid-provider."""
    from dataclasses import replace

    from aieos.platform.ai.domain.generation_run import GenerationRunStatus
    from aieos.platform.idempotency.hashing import hash_idempotency_key

    with SqlAlchemyAIUnitOfWorkFactory(runtime_engine)(tenant_id) as ai_uow:
        run = ai_uow.generation_runs.get_by_idempotency_key(
            principal_id=principal_id,
            idempotency_key_sha256=hash_idempotency_key(idempotency_key),
        )
        assert run is not None
        locked = ai_uow.generation_runs.get_for_update(run.generation_run_id)
        assert locked is not None
        failed = replace(
            locked,
            status=GenerationRunStatus.FAILED,
            failure_code="generation_lease_expired",
            lease_expires_at=None,
            completed_at=FIXED_NOW,
            updated_at=FIXED_NOW,
            aggregate_revision=locked.aggregate_revision + 1,
        )
        assert ai_uow.generation_runs.update(
            failed, expected_revision=locked.aggregate_revision
        )
        ai_uow.commit()


class PausingPreparationMaterializer:
    """Test-only wrapper: pause inside create() while I06 holds the AI row lock."""

    def __init__(
        self,
        inner: CreateAIPreparationArtifactsForReviewService,
        *,
        entered: object,
        proceed: object,
        after_success_raise: BaseException | None = None,
    ) -> None:
        self._inner = inner
        self._entered = entered
        self._proceed = proceed
        self._after_success_raise = after_success_raise
        self.create_calls = 0

    def create(self, *args, **kwargs):
        self.create_calls += 1
        self._entered.set()  # type: ignore[attr-defined]
        assert self._proceed.wait(timeout=30)  # type: ignore[attr-defined]
        result = self._inner.create(*args, **kwargs)
        if self._after_success_raise is not None:
            raise self._after_success_raise
        return result


def attach_pausing_materializer(
    service: PrepareTeachingWorkService,
    *,
    entered: object,
    proceed: object,
    after_success_raise: BaseException | None = None,
) -> PausingPreparationMaterializer:
    wrapper = PausingPreparationMaterializer(
        service._create_preparation_for_review,  # noqa: SLF001 — test seam
        entered=entered,
        proceed=proceed,
        after_success_raise=after_success_raise,
    )
    service._create_preparation_for_review = wrapper  # noqa: SLF001
    return wrapper

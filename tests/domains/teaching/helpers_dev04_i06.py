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

"""TOS-DEV04-I03 atomic six-artifact Content materialization PostgreSQL proofs."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.domains.content.application.ai_preparation_for_review import (
    CAPABILITY_EDUCATION_GENERATE_PREPARATION_KIT,
    CreateAIPreparationArtifactsForReviewCommand,
    CreateAIPreparationArtifactsForReviewService,
    GENERATION_RUN_RESOURCE_TYPE,
    PreparationProvenanceContext,
    WORK_RESOURCE_TYPE,
)
from aieos.domains.content.application.audit import ai_materialization_audit_provenance
from aieos.domains.content.application.catalog import StaticContentTypeCatalog
from aieos.domains.content.application.errors import (
    AIGenerationForbidden,
    AIPreparationArtifactsAlreadyMaterialized,
)
from aieos.domains.content.application.review_queue import ListTeacherReviewQueueService
from aieos.domains.content.application.review_queue_models import (
    ListTeacherReviewQueueQuery,
)
from aieos.domains.content.domain.identities import ContentId
from aieos.domains.content.domain.provenance import AIGenerationProvenanceV2
from aieos.domains.content.infrastructure.persistence.uow import (
    SqlAlchemyContentUnitOfWorkFactory,
)
from aieos.domains.education.content_payloads_v1 import (
    AnswerKeyV1,
    HomeworkV1,
    LessonPlanV1,
    QuizV1,
    TeacherNotesV1,
    WorksheetV1,
)
from aieos.domains.education.preparation_kit_v1 import (
    AnswerKeyEntryV1,
    AnswerKeySourceArtifactKind,
)
from aieos.domains.education.schema import (
    PREPARATION_ARTIFACT_KINDS,
    PREPARATION_CONTENT_TYPES,
    build_preparation_content_schema_registry,
)
from aieos.platform.events.models import MutationEventContext
from aieos.platform.resources import ResourceRef
from aieos.platform.security.audit import SecurityAuditAction
from tests.domains.education.test_tos_dev04_i03_content_payloads import (
    valid_homework_payload,
    valid_lesson_plan_payload,
    valid_quiz_payload,
    valid_teacher_notes_payload,
)
from tests.domains.teaching.worksheet_fixtures import valid_worksheet_model
from tests.fakes import AllowAIGenerationAuthorization, AllowAssetReferenceValidation

pytestmark = pytest.mark.tos_dev04_i03

FIXED_NOW = datetime(2026, 8, 29, 18, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _cleanup_i03_shared_db_rows(postgres18: dict[str, str]) -> None:
    """Remove V2 rows that would block other suites' Alembic downgrades."""
    from sqlalchemy import create_engine

    from tests.domains.teaching.test_tos_dev04_i02_multi_artifact_persistence import (
        _clear_i02_downgrade_blockers,
    )

    yield
    engine = create_engine(postgres18["bootstrap_url"])
    try:
        _clear_i02_downgrade_blockers(engine)
    finally:
        engine.dispose()


class FailOnNthAIGenerationAuthorization:
    """Test-only: fail after N successful authorizations (1-based)."""

    def __init__(self, *, fail_on_call: int) -> None:
        self._fail_on_call = fail_on_call
        self.calls = 0

    def authorize(
        self,
        *,
        tenant_id: uuid.UUID,
        principal_id: uuid.UUID,
        content_id: ContentId,
        capability: str,
    ) -> None:
        self.calls += 1
        if self.calls == self._fail_on_call:
            raise AIGenerationForbidden("injected mid-transaction authorization failure")


def _event_context(
    principal_id: uuid.UUID, correlation_id: uuid.UUID
) -> MutationEventContext:
    return MutationEventContext(
        correlation_id=correlation_id,
        causation_id=uuid.uuid7(),
        actor_principal_id=principal_id,
        effective_actor_id=principal_id,
    )


def _answer_key() -> AnswerKeyV1:
    return AnswerKeyV1(
        title="Answer key",
        entries=[
            AnswerKeyEntryV1(
                source_artifact_kind=AnswerKeySourceArtifactKind.WORKSHEET,
                source_question_id="q-1",
                answer="1/2",
                explanation="Half of a whole.",
            ),
            AnswerKeyEntryV1(
                source_artifact_kind=AnswerKeySourceArtifactKind.QUIZ,
                source_question_id="q-1",
                answer="1/2",
                explanation="Quiz half.",
            ),
            AnswerKeyEntryV1(
                source_artifact_kind=AnswerKeySourceArtifactKind.HOMEWORK,
                source_question_id="h-1",
                answer="1/2",
                explanation="Homework half.",
            ),
        ],
    )


def _command(
    *,
    work_id: uuid.UUID,
    work_revision: int,
    run_id: uuid.UUID,
    correlation_id: uuid.UUID,
) -> CreateAIPreparationArtifactsForReviewCommand:
    return CreateAIPreparationArtifactsForReviewCommand(
        lesson_plan=LessonPlanV1.model_validate(valid_lesson_plan_payload()),
        worksheet=valid_worksheet_model(),
        quiz=QuizV1.model_validate(valid_quiz_payload()),
        homework=HomeworkV1.model_validate(valid_homework_payload()),
        answer_key=_answer_key(),
        teacher_notes=TeacherNotesV1.model_validate(valid_teacher_notes_payload()),
        locale="en-IN",
        teacher_summary="Atomic preparation kit for fractions.",
        provenance=PreparationProvenanceContext(
            generation_run_ref=ResourceRef(
                GENERATION_RUN_RESOURCE_TYPE, run_id, 0
            ),
            prompt_execution_ref=None,
            provider_id="fake",
            model_id="fake-model",
            source_refs=(
                ResourceRef(WORK_RESOURCE_TYPE, work_id, work_revision),
            ),
            policy_refs=(),
            evaluation_refs=(),
            correlation_id=correlation_id,
        ),
    )


def _service(
    runtime_engine: Engine,
    *,
    authz=None,
) -> CreateAIPreparationArtifactsForReviewService:
    return CreateAIPreparationArtifactsForReviewService(
        SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
        StaticContentTypeCatalog(set(PREPARATION_CONTENT_TYPES)),
        build_preparation_content_schema_registry(),
        AllowAssetReferenceValidation(),
        authz if authz is not None else AllowAIGenerationAuthorization(),
    )


def _count(
    bootstrap_engine: Engine,
    sql: str,
    params: dict[str, object],
) -> int:
    with bootstrap_engine.connect() as conn:
        return int(conn.execute(text(sql), params).scalar_one())


class TestAtomicSixArtifactMaterialization:
    def test_success_six_of_six(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        work_id = uuid.uuid7()
        run_id = uuid.uuid7()
        correlation_id = uuid.uuid7()
        service = _service(runtime_engine)
        command = _command(
            work_id=work_id,
            work_revision=0,
            run_id=run_id,
            correlation_id=correlation_id,
        )
        result = service.create(
            tenant_id,
            principal_id,
            command,
            event_context=_event_context(principal_id, correlation_id),
            audit_provenance=ai_materialization_audit_provenance(principal_id),
            now=FIXED_NOW,
        )
        assert len(result.artifacts) == 6
        assert tuple(a.artifact_kind for a in result.artifacts) == PREPARATION_ARTIFACT_KINDS
        assert all(a.stewardship_state == "IN_REVIEW" for a in result.artifacts)

        params = {"tid": tenant_id, "rid": str(run_id)}
        assert _count(
            bootstrap_engine,
            "SELECT count(*) FROM content.contents WHERE tenant_id = :tid",
            params,
        ) == 6
        assert _count(
            bootstrap_engine,
            """
            SELECT count(*) FROM content.content_versions
             WHERE tenant_id = :tid
               AND origin = 'AI'
               AND provenance #>> '{generation_run_ref,resource_id}' = :rid
            """,
            params,
        ) == 6
        assert _count(
            bootstrap_engine,
            """
            SELECT count(*) FROM content.content_versions
             WHERE tenant_id = :tid
               AND (provenance->>'schema_version') = '2'
               AND provenance #>> '{generation_run_ref,resource_id}' = :rid
            """,
            params,
        ) == 6
        assert _count(
            bootstrap_engine,
            """
            SELECT count(*) FROM content.contents
             WHERE tenant_id = :tid AND stewardship_state = 'IN_REVIEW'
            """,
            params,
        ) == 6
        assert _count(
            bootstrap_engine,
            """
            SELECT count(*) FROM content.contents
             WHERE tenant_id = :tid
               AND current_version_id IS NOT NULL
            """,
            params,
        ) == 6
        assert _count(
            bootstrap_engine,
            """
            SELECT count(*) FROM content.content_versions
             WHERE tenant_id = :tid
               AND provenance #>> '{generation_run_ref,resource_id}' = :rid
               AND version_number = 1
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

        factory = SqlAlchemyContentUnitOfWorkFactory(runtime_engine)
        with factory(tenant_id) as uow:
            bindings = uow.versions.find_all_by_generation_run_id(run_id)
            assert len(bindings) == 6
            kinds = {b.artifact_kind for b in bindings}
            assert kinds == set(PREPARATION_ARTIFACT_KINDS)
            for binding in bindings:
                assert isinstance(binding.provenance, AIGenerationProvenanceV2)
                assert binding.provenance.capability_id == (
                    CAPABILITY_EDUCATION_GENERATE_PREPARATION_KIT
                )
                assert binding.provenance.correlation_id == correlation_id
                assert any(
                    ref.resource_type == WORK_RESOURCE_TYPE
                    and ref.resource_id == work_id
                    and ref.resource_revision == 0
                    for ref in binding.provenance.source_refs
                )
            assert uow.versions.find_by_generation_run_id(run_id) is None

        queue = ListTeacherReviewQueueService(factory).list(
            tenant_id,
            principal_id,
            ListTeacherReviewQueueQuery(limit=20),
        )
        assert len(queue.items) == 6
        assert all(item.artifact_status == "In Review" for item in queue.items)
        result_content_ids = {a.content_id.value for a in result.artifacts}
        result_version_ids = {a.version_id.value for a in result.artifacts}
        assert {item.content_id.value for item in queue.items} == result_content_ids
        assert {item.version_id.value for item in queue.items} == result_version_ids

        create_audits = _count(
            bootstrap_engine,
            """
            SELECT count(*) FROM security.audit_records
             WHERE tenant_id = :tid AND action = :action
            """,
            {
                "tid": tenant_id,
                "action": SecurityAuditAction.CONTENT_CREATE.value,
            },
        )
        materialize_audits = _count(
            bootstrap_engine,
            """
            SELECT count(*) FROM security.audit_records
             WHERE tenant_id = :tid AND action = :action
            """,
            {
                "tid": tenant_id,
                "action": SecurityAuditAction.CONTENT_AI_MATERIALIZE.value,
            },
        )
        review_audits = _count(
            bootstrap_engine,
            """
            SELECT count(*) FROM security.audit_records
             WHERE tenant_id = :tid AND action = :action
            """,
            {
                "tid": tenant_id,
                "action": SecurityAuditAction.CONTENT_REVIEW_SUBMIT.value,
            },
        )
        assert create_audits == 6
        assert materialize_audits == 6
        assert review_audits == 6
        outbox = _count(
            bootstrap_engine,
            "SELECT count(*) FROM integration.outbox_messages WHERE tenant_id = :tid",
            params,
        )
        assert outbox >= 18  # create + version + review intents per artifact

    def test_mid_transaction_authorization_failure_rolls_back_to_zero(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        """Fail on artifact 4 (homework) after earlier writes inside the same UoW."""
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        work_id = uuid.uuid7()
        run_id = uuid.uuid7()
        correlation_id = uuid.uuid7()
        authz = FailOnNthAIGenerationAuthorization(fail_on_call=4)
        service = _service(runtime_engine, authz=authz)
        command = _command(
            work_id=work_id,
            work_revision=1,
            run_id=run_id,
            correlation_id=correlation_id,
        )
        with pytest.raises(AIGenerationForbidden):
            service.create(
                tenant_id,
                principal_id,
                command,
                event_context=_event_context(principal_id, correlation_id),
                audit_provenance=ai_materialization_audit_provenance(principal_id),
                now=FIXED_NOW,
            )
        assert authz.calls == 4
        params = {"tid": tenant_id, "rid": str(run_id)}
        assert _count(
            bootstrap_engine,
            "SELECT count(*) FROM content.contents WHERE tenant_id = :tid",
            params,
        ) == 0
        assert _count(
            bootstrap_engine,
            """
            SELECT count(*) FROM content.content_versions
             WHERE tenant_id = :tid
               AND provenance #>> '{generation_run_ref,resource_id}' = :rid
            """,
            params,
        ) == 0
        assert _count(
            bootstrap_engine,
            "SELECT count(*) FROM content.review_decisions WHERE tenant_id = :tid",
            params,
        ) == 0
        assert _count(
            bootstrap_engine,
            "SELECT count(*) FROM content.publications WHERE tenant_id = :tid",
            params,
        ) == 0
        assert _count(
            bootstrap_engine,
            "SELECT count(*) FROM integration.outbox_messages WHERE tenant_id = :tid",
            params,
        ) == 0
        assert _count(
            bootstrap_engine,
            "SELECT count(*) FROM security.audit_records WHERE tenant_id = :tid",
            params,
        ) == 0
        factory = SqlAlchemyContentUnitOfWorkFactory(runtime_engine)
        with factory(tenant_id) as uow:
            assert uow.versions.find_all_by_generation_run_id(run_id) == []
        queue = ListTeacherReviewQueueService(factory).list(
            tenant_id,
            principal_id,
            ListTeacherReviewQueueQuery(limit=20),
        )
        assert queue.items == ()

    def test_duplicate_materialization_fails_closed(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        work_id = uuid.uuid7()
        run_id = uuid.uuid7()
        correlation_id = uuid.uuid7()
        service = _service(runtime_engine)
        command = _command(
            work_id=work_id,
            work_revision=0,
            run_id=run_id,
            correlation_id=correlation_id,
        )
        event = _event_context(principal_id, correlation_id)
        audit = ai_materialization_audit_provenance(principal_id)
        service.create(
            tenant_id,
            principal_id,
            command,
            event_context=event,
            audit_provenance=audit,
            now=FIXED_NOW,
        )
        with pytest.raises(AIPreparationArtifactsAlreadyMaterialized):
            service.create(
                tenant_id,
                principal_id,
                command,
                event_context=event,
                audit_provenance=audit,
                now=FIXED_NOW,
            )
        assert _count(
            bootstrap_engine,
            "SELECT count(*) FROM content.contents WHERE tenant_id = :tid",
            {"tid": tenant_id},
        ) == 6
        assert _count(
            bootstrap_engine,
            """
            SELECT count(*) FROM content.content_versions
             WHERE tenant_id = :tid
               AND provenance #>> '{generation_run_ref,resource_id}' = :rid
            """,
            {"tid": tenant_id, "rid": str(run_id)},
        ) == 6

    def test_tenant_isolation_on_bindings_and_queue(
        self, runtime_engine: Engine
    ) -> None:
        tenant_a = uuid.uuid7()
        tenant_b = uuid.uuid7()
        principal_a = uuid.uuid7()
        principal_b = uuid.uuid7()
        work_id = uuid.uuid7()
        run_id = uuid.uuid7()
        correlation_id = uuid.uuid7()
        service = _service(runtime_engine)
        command = _command(
            work_id=work_id,
            work_revision=0,
            run_id=run_id,
            correlation_id=correlation_id,
        )
        service.create(
            tenant_a,
            principal_a,
            command,
            event_context=_event_context(principal_a, correlation_id),
            audit_provenance=ai_materialization_audit_provenance(principal_a),
            now=FIXED_NOW,
        )
        factory = SqlAlchemyContentUnitOfWorkFactory(runtime_engine)
        with factory(tenant_b) as uow:
            assert uow.versions.find_all_by_generation_run_id(run_id) == []
        queue_b = ListTeacherReviewQueueService(factory).list(
            tenant_b,
            principal_b,
            ListTeacherReviewQueueQuery(limit=20),
        )
        assert queue_b.items == ()
        queue_a = ListTeacherReviewQueueService(factory).list(
            tenant_a,
            principal_a,
            ListTeacherReviewQueueQuery(limit=20),
        )
        assert len(queue_a.items) == 6

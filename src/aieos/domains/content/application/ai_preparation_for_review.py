"""Atomic six-artifact AI Content materialization for review (TOS-DEV04-I03).

One Content UoW / one PostgreSQL transaction / one commit for all six artifacts.
Does not invoke CreateAIGeneratedContentForReviewService (that path is one-artifact /
one-transaction and would violate ADR-AIEOS-052 atomicity).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Mapping
from uuid import UUID

from aieos.domains.content.application.audit import (
    MutationAuditProvenance,
    content_version_ref,
    insert_required_content_audit,
)
from aieos.domains.content.application.errors import (
    AIPreparationArtifactsAlreadyMaterialized,
    AIPreparationArtifactsInvalid,
    AIProvenanceInvalid,
    UnknownContentType,
)
from aieos.domains.content.application.in_uow import (
    create_content_in_uow,
    materialize_ai_version_in_uow,
)
from aieos.domains.content.application.models import (
    AIGeneratedVersionMaterializationCommand,
    CreateContentCommand,
)
from aieos.domains.content.application.ports import (
    CONTENT_VERSION_CREATE,
    AIGenerationAuthorizationPort,
    AssetReferenceValidationPort,
    ContentTypeCatalog,
    ContentUnitOfWorkFactory,
)
from aieos.domains.content.application.review import submit_for_review_in_uow
from aieos.domains.content.domain.identities import AggregateRevision, ContentId, ContentVersionId
from aieos.domains.content.domain.provenance import AIGenerationProvenanceV2
from aieos.domains.content.domain.errors import SchemaNotFoundError
from aieos.domains.content.domain.schema import ContentSchemaRegistry
from aieos.domains.content.domain.states import StewardshipState
from aieos.domains.education.content_payloads_v1 import (
    AnswerKeyV1,
    HomeworkV1,
    LessonPlanV1,
    QuizV1,
    TeacherNotesV1,
    WorksheetV1,
)
from aieos.domains.education.schema import (
    ANSWER_KEY_CONTENT_TYPE,
    ANSWER_KEY_SCHEMA_ID,
    ANSWER_KEY_SCHEMA_VERSION,
    HOMEWORK_CONTENT_TYPE,
    HOMEWORK_SCHEMA_ID,
    HOMEWORK_SCHEMA_VERSION,
    LESSON_PLAN_CONTENT_TYPE,
    LESSON_PLAN_SCHEMA_ID,
    LESSON_PLAN_SCHEMA_VERSION,
    PREPARATION_ARTIFACT_KINDS,
    PREPARATION_CONTENT_TYPES,
    QUIZ_CONTENT_TYPE,
    QUIZ_SCHEMA_ID,
    QUIZ_SCHEMA_VERSION,
    TEACHER_NOTES_CONTENT_TYPE,
    TEACHER_NOTES_SCHEMA_ID,
    TEACHER_NOTES_SCHEMA_VERSION,
    WORKSHEET_CONTENT_TYPE,
    WORKSHEET_SCHEMA_ID,
    WORKSHEET_SCHEMA_VERSION,
)
from aieos.platform.events.models import MutationEventContext
from aieos.platform.resources import ResourceRef
from aieos.platform.security.audit import SecurityAuditAction

CAPABILITY_EDUCATION_GENERATE_PREPARATION_KIT = "education.generate_preparation_kit"
WORK_RESOURCE_TYPE = "teaching.work"
GENERATION_RUN_RESOURCE_TYPE = "ai.generation_run"

_SCHEMA_BY_KIND: Mapping[str, tuple[str, str, int]] = {
    "lesson_plan": (
        LESSON_PLAN_CONTENT_TYPE,
        LESSON_PLAN_SCHEMA_ID,
        LESSON_PLAN_SCHEMA_VERSION,
    ),
    "worksheet": (
        WORKSHEET_CONTENT_TYPE,
        WORKSHEET_SCHEMA_ID,
        WORKSHEET_SCHEMA_VERSION,
    ),
    "quiz": (QUIZ_CONTENT_TYPE, QUIZ_SCHEMA_ID, QUIZ_SCHEMA_VERSION),
    "homework": (
        HOMEWORK_CONTENT_TYPE,
        HOMEWORK_SCHEMA_ID,
        HOMEWORK_SCHEMA_VERSION,
    ),
    "answer_key": (
        ANSWER_KEY_CONTENT_TYPE,
        ANSWER_KEY_SCHEMA_ID,
        ANSWER_KEY_SCHEMA_VERSION,
    ),
    "teacher_notes": (
        TEACHER_NOTES_CONTENT_TYPE,
        TEACHER_NOTES_SCHEMA_ID,
        TEACHER_NOTES_SCHEMA_VERSION,
    ),
}


@dataclass(frozen=True, slots=True)
class PreparationProvenanceContext:
    """Common provenance fields shared by all six preparation artifacts."""

    generation_run_ref: ResourceRef
    prompt_execution_ref: ResourceRef | None
    provider_id: str
    model_id: str
    source_refs: tuple[ResourceRef, ...]
    policy_refs: tuple[ResourceRef, ...]
    evaluation_refs: tuple[ResourceRef, ...]
    correlation_id: UUID


@dataclass(frozen=True, slots=True)
class CreateAIPreparationArtifactsForReviewCommand:
    """In-memory composite command — not a durable PreparationKit aggregate."""

    lesson_plan: LessonPlanV1
    worksheet: WorksheetV1
    quiz: QuizV1
    homework: HomeworkV1
    answer_key: AnswerKeyV1
    teacher_notes: TeacherNotesV1
    locale: str
    teacher_summary: str
    provenance: PreparationProvenanceContext


@dataclass(frozen=True, slots=True)
class PreparationArtifactResult:
    artifact_kind: str
    content_id: ContentId
    version_id: ContentVersionId
    content_type: str
    title: str
    stewardship_state: str
    aggregate_revision: AggregateRevision


@dataclass(frozen=True, slots=True)
class CreateAIPreparationArtifactsForReviewResult:
    artifacts: tuple[PreparationArtifactResult, ...]


@dataclass(frozen=True, slots=True)
class _ArtifactSpec:
    artifact_kind: str
    content_type: str
    schema_id: str
    schema_version: int
    title: str
    payload: dict[str, object]


def _require_exact_work_revision(
    source_refs: tuple[ResourceRef, ...],
) -> ResourceRef:
    work_refs = [
        ref
        for ref in source_refs
        if ref.resource_type == WORK_RESOURCE_TYPE
    ]
    if len(work_refs) != 1:
        raise AIPreparationArtifactsInvalid(
            "provenance source_refs must contain exactly one teaching.work reference"
        )
    work_ref = work_refs[0]
    if work_ref.resource_revision is None:
        raise AIPreparationArtifactsInvalid(
            "teaching.work resource_revision must be exact (not null)"
        )
    return work_ref


def _build_artifact_specs(
    command: CreateAIPreparationArtifactsForReviewCommand,
) -> tuple[_ArtifactSpec, ...]:
    payloads: dict[str, tuple[str, dict[str, object]]] = {
        "lesson_plan": (
            command.lesson_plan.title,
            command.lesson_plan.model_dump(mode="json"),
        ),
        "worksheet": (
            command.worksheet.title,
            command.worksheet.model_dump(mode="json"),
        ),
        "quiz": (command.quiz.title, command.quiz.model_dump(mode="json")),
        "homework": (
            command.homework.title,
            command.homework.model_dump(mode="json"),
        ),
        "answer_key": (
            command.answer_key.title,
            command.answer_key.model_dump(mode="json"),
        ),
        "teacher_notes": (
            command.teacher_notes.title,
            command.teacher_notes.model_dump(mode="json"),
        ),
    }
    specs: list[_ArtifactSpec] = []
    for kind in PREPARATION_ARTIFACT_KINDS:
        content_type, schema_id, schema_version = _SCHEMA_BY_KIND[kind]
        title, payload = payloads[kind]
        specs.append(
            _ArtifactSpec(
                artifact_kind=kind,
                content_type=content_type,
                schema_id=schema_id,
                schema_version=schema_version,
                title=title,
                payload=payload,
            )
        )
    return tuple(specs)


def _provenance_for_kind(
    context: PreparationProvenanceContext,
    *,
    artifact_kind: str,
) -> AIGenerationProvenanceV2:
    return AIGenerationProvenanceV2(
        generation_run_ref=context.generation_run_ref,
        prompt_execution_ref=context.prompt_execution_ref,
        provider_id=context.provider_id,
        model_id=context.model_id,
        capability_id=CAPABILITY_EDUCATION_GENERATE_PREPARATION_KIT,
        source_refs=context.source_refs,
        policy_refs=context.policy_refs,
        evaluation_refs=context.evaluation_refs,
        correlation_id=context.correlation_id,
        artifact_kind=artifact_kind,
    )


class CreateAIPreparationArtifactsForReviewService:
    """Create six Content aggregates + AI versions + IN_REVIEW in one transaction."""

    def __init__(
        self,
        uow_factory: ContentUnitOfWorkFactory,
        catalog: ContentTypeCatalog,
        schema_registry: ContentSchemaRegistry,
        asset_reference_validation: AssetReferenceValidationPort,
        ai_generation_authorization: AIGenerationAuthorizationPort,
    ) -> None:
        self._uow_factory = uow_factory
        self._catalog = catalog
        self._schema_registry = schema_registry
        self._asset_reference_validation = asset_reference_validation
        self._ai_generation_authorization = ai_generation_authorization

    def create(
        self,
        execution_tenant_id: UUID,
        principal_id: UUID,
        command: CreateAIPreparationArtifactsForReviewCommand,
        *,
        event_context: MutationEventContext,
        audit_provenance: MutationAuditProvenance,
        now: datetime | None = None,
    ) -> CreateAIPreparationArtifactsForReviewResult:
        specs = _build_artifact_specs(command)
        self._prevalidate(command, specs, event_context=event_context)
        created_at = now if now is not None else datetime.now(UTC)
        generation_run_id = command.provenance.generation_run_ref.resource_id

        with self._uow_factory(execution_tenant_id) as uow:
            existing = uow.versions.find_all_by_generation_run_id(generation_run_id)
            if existing:
                raise AIPreparationArtifactsAlreadyMaterialized(
                    "GenerationRun already has AI ContentVersion bindings; "
                    "I03 refuses rematerialization (recovery belongs to I06)"
                )
            try:
                results: list[PreparationArtifactResult] = []
                for spec in specs:
                    content = create_content_in_uow(
                        uow,
                        execution_tenant_id,
                        principal_id,
                        CreateContentCommand(
                            content_type=spec.content_type,
                            title=spec.title,
                            description=command.teacher_summary,
                            locale=command.locale,
                        ),
                        event_context=event_context,
                        audit_provenance=audit_provenance,
                        created_at=created_at,
                    )
                    self._ai_generation_authorization.authorize(
                        tenant_id=execution_tenant_id,
                        principal_id=principal_id,
                        content_id=content.content_id,
                        capability=CONTENT_VERSION_CREATE,
                    )
                    provenance = _provenance_for_kind(
                        command.provenance,
                        artifact_kind=spec.artifact_kind,
                    )
                    materialized = materialize_ai_version_in_uow(
                        uow,
                        execution_tenant_id,
                        principal_id,
                        AIGeneratedVersionMaterializationCommand(
                            content_id=content.content_id,
                            expected_aggregate_revision=AggregateRevision(0),
                            schema_id=spec.schema_id,
                            schema_version=spec.schema_version,
                            payload=spec.payload,
                            provenance=provenance,
                            asset_refs=(),
                        ),
                        schema_registry=self._schema_registry,
                        asset_reference_validation=self._asset_reference_validation,
                        event_context=event_context,
                        audit_provenance=audit_provenance,
                        created_at=created_at,
                    )
                    revision = submit_for_review_in_uow(
                        uow,
                        execution_tenant_id,
                        content_id=content.content_id,
                        version_id=materialized.version_id,
                        expected_aggregate_revision=materialized.aggregate_revision,
                        event_context=event_context,
                        updated_at=created_at,
                    )
                    insert_required_content_audit(
                        uow,
                        tenant_id=execution_tenant_id,
                        action=SecurityAuditAction.CONTENT_REVIEW_SUBMIT,
                        content_id=content.content_id.value,
                        resource_revision_before=int(materialized.aggregate_revision),
                        resource_revision_after=int(revision),
                        related_resource_refs=(
                            content_version_ref(materialized.version_id.value),
                        ),
                        mutation_event_context=event_context,
                        audit_provenance=audit_provenance,
                        occurred_at=created_at,
                    )
                    results.append(
                        PreparationArtifactResult(
                            artifact_kind=spec.artifact_kind,
                            content_id=content.content_id,
                            version_id=materialized.version_id,
                            content_type=spec.content_type,
                            title=spec.title,
                            stewardship_state=StewardshipState.IN_REVIEW.value,
                            aggregate_revision=revision,
                        )
                    )
                uow.commit()
            except Exception:
                uow.rollback()
                raise

        return CreateAIPreparationArtifactsForReviewResult(artifacts=tuple(results))

    def _prevalidate(
        self,
        command: CreateAIPreparationArtifactsForReviewCommand,
        specs: tuple[_ArtifactSpec, ...],
        *,
        event_context: MutationEventContext,
    ) -> None:
        if tuple(spec.artifact_kind for spec in specs) != PREPARATION_ARTIFACT_KINDS:
            raise AIPreparationArtifactsInvalid(
                "artifact kinds must be exactly the six baseline preparation kinds"
            )
        if set(PREPARATION_CONTENT_TYPES) != {spec.content_type for spec in specs}:
            raise AIPreparationArtifactsInvalid(
                "content types must be exactly the six preparation types"
            )
        for content_type in PREPARATION_CONTENT_TYPES:
            if not self._catalog.contains(content_type):
                raise UnknownContentType(
                    f"content_type {content_type!r} is not registered"
                )
        for spec in specs:
            try:
                registered = self._schema_registry.get(
                    spec.schema_id, spec.schema_version
                )
            except SchemaNotFoundError as exc:
                raise AIPreparationArtifactsInvalid(str(exc)) from exc
            if registered.content_type != spec.content_type:
                raise AIPreparationArtifactsInvalid(
                    f"schema {spec.schema_id}@{spec.schema_version} content_type mismatch"
                )
            registered.validate(spec.payload)

        context = command.provenance
        if context.generation_run_ref.resource_type != GENERATION_RUN_RESOURCE_TYPE:
            raise AIPreparationArtifactsInvalid(
                "generation_run_ref.resource_type must be ai.generation_run"
            )
        _require_exact_work_revision(context.source_refs)
        if context.correlation_id != event_context.correlation_id:
            raise AIProvenanceInvalid(
                "provenance.correlation_id must match MutationEventContext.correlation_id"
            )
        if not command.teacher_summary.strip():
            raise AIPreparationArtifactsInvalid("teacher_summary must not be blank")
        if not command.locale.strip():
            raise AIPreparationArtifactsInvalid("locale must not be blank")
        for kind in PREPARATION_ARTIFACT_KINDS:
            _provenance_for_kind(context, artifact_kind=kind)

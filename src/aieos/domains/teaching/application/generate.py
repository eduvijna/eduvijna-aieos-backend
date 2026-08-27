"""Teaching Work → AI worksheet generation orchestration (TOS-DEV03)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Mapping
from uuid import UUID

from aieos.domains.content.application.ai_for_review import (
    CreateAIGeneratedContentForReviewCommand,
    CreateAIGeneratedContentForReviewResult,
    CreateAIGeneratedContentForReviewService,
)
from aieos.domains.content.application.audit import (
    MutationAuditProvenance,
    ai_materialization_audit_provenance,
)
from aieos.domains.content.domain.provenance import AIGenerationProvenanceV1
from aieos.domains.education.application.generate_worksheet import (
    EducationalQualityFailed,
    GenerateWorksheetCapability,
)
from aieos.domains.education.application.models import WorksheetGenerationInput
from aieos.domains.education.schema import (
    WORKSHEET_CONTENT_TYPE,
    WORKSHEET_SCHEMA_ID,
    WORKSHEET_SCHEMA_VERSION,
)
from aieos.domains.teaching.application.errors import (
    EducationalQualityFailedError,
    GenerationIdempotencyConflict,
    ModelGenerationFailedError,
    ModelOutputInvalidError,
    ModelProviderUnavailableError,
    TeachingWorkForbidden,
    TeachingWorkNotFound,
    WorkGenerationAlreadyExists,
    WorkGenerationInProgress,
    WorkGenerationRevisionConflict,
)
from aieos.domains.teaching.application.ports import TeachingUnitOfWorkFactory
from aieos.domains.teaching.domain.identities import AggregateRevision, WorkId
from aieos.platform.ai.application.ports import AIUnitOfWorkFactory
from aieos.platform.ai.domain.generation_run import (
    GenerationRun,
    GenerationRunId,
    GenerationRunStatus,
)
from aieos.platform.ai.gateway import (
    ModelGenerationFailed,
    ModelOutputInvalid,
    ModelProviderUnavailable,
)
from aieos.platform.capabilities.models import CAPABILITY_EDUCATION_GENERATE_WORKSHEET
from aieos.platform.education.quality_baseline import (
    EducationalQualityResult,
    educational_quality_from_summary,
)
from aieos.platform.events.models import MutationEventContext
from aieos.platform.idempotency.hashing import fingerprint_material, hash_idempotency_key
from aieos.platform.resources import ResourceRef

WORK_RESOURCE_TYPE = "teaching.work"
GENERATION_RUN_RESOURCE_TYPE = "ai.generation_run"


@dataclass(frozen=True, slots=True)
class EducationalQualityView:
    status: str
    checks: tuple[dict[str, object], ...]


@dataclass(frozen=True, slots=True)
class GeneratedArtifactView:
    content_id: UUID
    version_id: UUID
    content_type: str
    title: str
    stewardship_state: str
    aggregate_revision: int


@dataclass(frozen=True, slots=True)
class GenerateTeachingWorkResult:
    work_id: WorkId
    generation_run_id: GenerationRunId
    artifact: GeneratedArtifactView
    educational_quality: EducationalQualityView


def _quality_view(result: EducationalQualityResult) -> EducationalQualityView:
    return EducationalQualityView(
        status=result.status.value,
        checks=tuple(
            {
                "code": check.code,
                "passed": check.passed,
                "explanation": check.explanation,
            }
            for check in result.checks
        ),
    )


def _quality_view_from_summary(
    summary: Mapping[str, object] | None,
) -> EducationalQualityView:
    parsed = educational_quality_from_summary(summary)
    if parsed is None:
        return EducationalQualityView(status="PASS", checks=())
    return _quality_view(parsed)


def _generation_fingerprint(
    *,
    work_id: WorkId,
    work_revision: int,
    capability_id: str,
    provider_id: str,
    model_id: str,
) -> str:
    return fingerprint_material(
        {
            "work_id": str(work_id),
            "work_revision": work_revision,
            "capability_id": capability_id,
            "provider_id": provider_id,
            "model_id": model_id,
        }
    )


class GenerateTeachingWorkService:
    """Orchestrate Work revision gate → GenerationRun → capability → Content+Review."""

    def __init__(
        self,
        teaching_uow_factory: TeachingUnitOfWorkFactory,
        ai_uow_factory: AIUnitOfWorkFactory,
        worksheet_capability: GenerateWorksheetCapability,
        create_ai_content_for_review: CreateAIGeneratedContentForReviewService,
        *,
        provider_id: str,
        model_id: str,
    ) -> None:
        self._teaching_uow_factory = teaching_uow_factory
        self._ai_uow_factory = ai_uow_factory
        self._worksheet_capability = worksheet_capability
        self._create_ai_content_for_review = create_ai_content_for_review
        self._provider_id = provider_id
        self._model_id = model_id

    def generate(
        self,
        execution_tenant_id: UUID,
        principal_id: UUID,
        *,
        work_id: WorkId,
        expected_aggregate_revision: AggregateRevision,
        idempotency_key: str,
        event_context: MutationEventContext,
        now: datetime | None = None,
    ) -> GenerateTeachingWorkResult:
        decided_at = now if now is not None else datetime.now(UTC)
        key_hash = hash_idempotency_key(idempotency_key)

        with self._teaching_uow_factory(execution_tenant_id) as teaching_uow:
            work = teaching_uow.works.get(work_id)
            if work is None or work.tenant_id != execution_tenant_id:
                raise TeachingWorkNotFound("Teaching Work was not found")
            if work.teacher_principal_id != principal_id:
                raise TeachingWorkForbidden("Teaching Work is owned by a different teacher")
            if work.aggregate_revision != expected_aggregate_revision:
                raise WorkGenerationRevisionConflict(
                    "If-Match does not match the current Work revision"
                )
            work_snapshot = work

        fingerprint = _generation_fingerprint(
            work_id=work_id,
            work_revision=int(expected_aggregate_revision),
            capability_id=CAPABILITY_EDUCATION_GENERATE_WORKSHEET,
            provider_id=self._provider_id,
            model_id=self._model_id,
        )

        with self._ai_uow_factory(execution_tenant_id) as ai_uow:
            existing_success = ai_uow.generation_runs.find_succeeded_for_work(
                principal_id=principal_id,
                work_resource_id=work_id.value,
            )
            by_key = ai_uow.generation_runs.get_by_idempotency_key(
                principal_id=principal_id,
                idempotency_key_sha256=key_hash,
            )

            if by_key is not None:
                if by_key.request_fingerprint_sha256 != fingerprint:
                    raise GenerationIdempotencyConflict(
                        "Idempotency-Key was already used with a different request"
                    )
                if by_key.status is GenerationRunStatus.RUNNING:
                    raise WorkGenerationInProgress("work generation is in progress")
                if by_key.status is GenerationRunStatus.FAILED:
                    code = by_key.failure_code or "model_generation_failed"
                    if code == "educational_quality_failed":
                        raise EducationalQualityFailedError(
                            educational_quality=_quality_view_from_summary(
                                by_key.educational_quality_summary
                            )
                        )
                    if code == "model_provider_unavailable":
                        raise ModelProviderUnavailableError("model provider unavailable")
                    if code == "model_output_invalid":
                        raise ModelOutputInvalidError("model output invalid")
                    raise ModelGenerationFailedError(code)
                if by_key.status is GenerationRunStatus.SUCCEEDED:
                    return self._result_from_run(work_id, by_key)
                if by_key.status is GenerationRunStatus.VALIDATED:
                    raise WorkGenerationInProgress("work generation is in progress")

            if existing_success is not None and (
                by_key is None or by_key.generation_run_id != existing_success.generation_run_id
            ):
                err = WorkGenerationAlreadyExists()
                err.existing_generation_run_id = existing_success.generation_run_id
                err.existing_content_id = existing_success.result_content_id
                err.existing_version_id = existing_success.result_version_id
                raise err

            run = GenerationRun(
                generation_run_id=GenerationRunId.generate(),
                tenant_id=execution_tenant_id,
                principal_id=principal_id,
                work_resource_type=WORK_RESOURCE_TYPE,
                work_resource_id=work_id.value,
                work_resource_revision=int(expected_aggregate_revision),
                capability_id=CAPABILITY_EDUCATION_GENERATE_WORKSHEET,
                provider_id=self._provider_id,
                model_id=self._model_id,
                status=GenerationRunStatus.RUNNING,
                request_fingerprint_sha256=fingerprint,
                idempotency_key_sha256=key_hash,
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
                created_at=decided_at,
                updated_at=decided_at,
                completed_at=None,
            )
            ai_uow.generation_runs.insert(run)
            ai_uow.commit()
            run_id = run.generation_run_id

        generation_input = WorksheetGenerationInput(
            work_ref=ResourceRef(
                WORK_RESOURCE_TYPE,
                work_id.value,
                int(expected_aggregate_revision),
            ),
            goal_text=work_snapshot.goal_text,
            class_label=work_snapshot.class_label,
            subject=work_snapshot.subject,
            topic=work_snapshot.topic,
            target_date=work_snapshot.target_date,
            locale=work_snapshot.locale,
        )

        try:
            draft = self._worksheet_capability.execute(generation_input)
        except EducationalQualityFailed as exc:
            self._mark_failed(
                execution_tenant_id,
                run_id,
                failure_code="educational_quality_failed",
                educational_quality_summary=exc.draft.educational_quality_result.as_summary(),
                provider_metadata=exc.draft.provider_metadata,
                now=decided_at,
            )
            raise EducationalQualityFailedError(
                educational_quality=_quality_view(exc.draft.educational_quality_result)
            ) from exc
        except ModelProviderUnavailable as exc:
            self._mark_failed(
                execution_tenant_id,
                run_id,
                failure_code="model_provider_unavailable",
                now=decided_at,
            )
            raise ModelProviderUnavailableError("model provider unavailable") from exc
        except ModelOutputInvalid as exc:
            self._mark_failed(
                execution_tenant_id,
                run_id,
                failure_code="model_output_invalid",
                now=decided_at,
            )
            raise ModelOutputInvalidError("model output invalid") from exc
        except ModelGenerationFailed as exc:
            self._mark_failed(
                execution_tenant_id,
                run_id,
                failure_code="model_generation_failed",
                now=decided_at,
            )
            raise ModelGenerationFailedError("model generation failed") from exc

        quality_summary = draft.educational_quality_result.as_summary()
        meta = draft.provider_metadata

        with self._ai_uow_factory(execution_tenant_id) as ai_uow:
            locked = ai_uow.generation_runs.get_for_update(run_id)
            if locked is None:
                raise TeachingWorkNotFound("GenerationRun was not found")
            validated = replace(
                locked,
                status=GenerationRunStatus.VALIDATED,
                provider_id=meta.provider_id,
                model_id=meta.model_id,
                provider_response_id=meta.provider_response_id,
                input_tokens=meta.input_tokens,
                output_tokens=meta.output_tokens,
                total_tokens=meta.total_tokens,
                educational_quality_summary=quality_summary,
                aggregate_revision=1,
                updated_at=decided_at,
            )
            if not ai_uow.generation_runs.update(validated, expected_revision=0):
                raise WorkGenerationInProgress("work generation concurrency conflict")
            ai_uow.commit()

        provenance = AIGenerationProvenanceV1(
            generation_run_ref=ResourceRef(GENERATION_RUN_RESOURCE_TYPE, run_id.value, 1),
            prompt_execution_ref=None,
            provider_id=meta.provider_id,
            model_id=meta.model_id,
            capability_id=CAPABILITY_EDUCATION_GENERATE_WORKSHEET,
            source_refs=(
                ResourceRef(
                    WORK_RESOURCE_TYPE,
                    work_id.value,
                    int(expected_aggregate_revision),
                ),
            ),
            policy_refs=(),
            evaluation_refs=(),
            correlation_id=event_context.correlation_id,
        )
        audit = ai_materialization_audit_provenance(principal_id)
        try:
            materialization = self._create_ai_content_for_review.create(
                execution_tenant_id,
                principal_id,
                CreateAIGeneratedContentForReviewCommand(
                    content_type=WORKSHEET_CONTENT_TYPE,
                    title=draft.worksheet_payload.title,
                    description=draft.worksheet_payload.teacher_summary,
                    locale=work_snapshot.locale,
                    schema_id=WORKSHEET_SCHEMA_ID,
                    schema_version=WORKSHEET_SCHEMA_VERSION,
                    payload=draft.worksheet_payload.model_dump(mode="json"),
                    provenance=provenance,
                ),
                event_context=event_context,
                audit_provenance=audit,
                now=decided_at,
            )
        except Exception as exc:
            self._mark_failed(
                execution_tenant_id,
                run_id,
                failure_code="model_generation_failed",
                educational_quality_summary=quality_summary,
                provider_metadata=meta,
                now=decided_at,
            )
            raise ModelGenerationFailedError("content materialization failed") from exc

        with self._ai_uow_factory(execution_tenant_id) as ai_uow:
            locked = ai_uow.generation_runs.get_for_update(run_id)
            if locked is None:
                raise TeachingWorkNotFound("GenerationRun was not found")
            succeeded = replace(
                locked,
                status=GenerationRunStatus.SUCCEEDED,
                result_content_id=materialization.content_id.value,
                result_version_id=materialization.version_id.value,
                result_content_revision=int(materialization.aggregate_revision),
                educational_quality_summary=quality_summary,
                aggregate_revision=2,
                updated_at=decided_at,
                completed_at=decided_at,
            )
            if not ai_uow.generation_runs.update(succeeded, expected_revision=1):
                raise WorkGenerationInProgress("work generation concurrency conflict")
            ai_uow.commit()
            return self._result_from_run(work_id, succeeded, materialization=materialization)

    def _mark_failed(
        self,
        execution_tenant_id: UUID,
        run_id: GenerationRunId,
        *,
        failure_code: str,
        educational_quality_summary: Mapping[str, object] | None = None,
        provider_metadata: object | None = None,
        now: datetime,
    ) -> None:
        with self._ai_uow_factory(execution_tenant_id) as ai_uow:
            locked = ai_uow.generation_runs.get_for_update(run_id)
            if locked is None:
                return
            meta = provider_metadata
            failed = replace(
                locked,
                status=GenerationRunStatus.FAILED,
                failure_code=failure_code,
                educational_quality_summary=(
                    educational_quality_summary
                    if educational_quality_summary is not None
                    else locked.educational_quality_summary
                ),
                provider_id=getattr(meta, "provider_id", locked.provider_id),
                model_id=getattr(meta, "model_id", locked.model_id),
                provider_response_id=getattr(
                    meta, "provider_response_id", locked.provider_response_id
                ),
                input_tokens=getattr(meta, "input_tokens", locked.input_tokens),
                output_tokens=getattr(meta, "output_tokens", locked.output_tokens),
                total_tokens=getattr(meta, "total_tokens", locked.total_tokens),
                aggregate_revision=locked.aggregate_revision + 1,
                updated_at=now,
                completed_at=now,
            )
            ai_uow.generation_runs.update(
                failed, expected_revision=locked.aggregate_revision
            )
            ai_uow.commit()

    def _result_from_run(
        self,
        work_id: WorkId,
        run: GenerationRun,
        *,
        materialization: CreateAIGeneratedContentForReviewResult | None = None,
    ) -> GenerateTeachingWorkResult:
        if run.result_content_id is None or run.result_version_id is None:
            raise WorkGenerationInProgress("generation result refs are not ready")
        if materialization is not None:
            artifact = GeneratedArtifactView(
                content_id=materialization.content_id.value,
                version_id=materialization.version_id.value,
                content_type=materialization.content_type,
                title=materialization.title,
                stewardship_state=materialization.stewardship_state,
                aggregate_revision=int(materialization.aggregate_revision),
            )
        else:
            artifact = GeneratedArtifactView(
                content_id=run.result_content_id,
                version_id=run.result_version_id,
                content_type=WORKSHEET_CONTENT_TYPE,
                title="",
                stewardship_state="IN_REVIEW",
                aggregate_revision=run.result_content_revision or 0,
            )
        return GenerateTeachingWorkResult(
            work_id=work_id,
            generation_run_id=run.generation_run_id,
            artifact=artifact,
            educational_quality=_quality_view_from_summary(run.educational_quality_summary),
        )

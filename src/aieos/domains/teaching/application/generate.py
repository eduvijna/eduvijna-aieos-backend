"""Teaching Work → AI worksheet generation orchestration (TOS-DEV03 / TOS-DEV03R1)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Mapping
from uuid import UUID

from aieos.domains.content.application.ai_for_review import (
    CreateAIGeneratedContentForReviewCommand,
    CreateAIGeneratedContentForReviewResult,
    CreateAIGeneratedContentForReviewService,
    find_content_version_by_generation_run_id,
)
from aieos.domains.content.application.audit import (
    ai_materialization_audit_provenance,
)
from aieos.domains.content.application.errors import (
    AIGenerationForbidden,
    ContentApplicationError,
)
from aieos.domains.content.application.ports import ContentUnitOfWorkFactory
from aieos.domains.content.domain.identities import (
    AggregateRevision as ContentAggregateRevision,
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
    ContentMaterializationFailedError,
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
from aieos.platform.ai.application.errors import GenerationRunConflict
from aieos.platform.ai.application.ports import AIUnitOfWorkFactory
from aieos.platform.ai.clock import UtcNow, utc_now
from aieos.platform.ai.config import DEFAULT_GENERATION_LEASE_SECONDS
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


def _lease_fresh(run: GenerationRun, *, now: datetime) -> bool:
    if run.lease_expires_at is None:
        return False
    return run.lease_expires_at > now


class GenerateTeachingWorkService:
    """Orchestrate Work revision gate → GenerationRun → capability → Content+Review.

    Durable statuses only: RUNNING → SUCCEEDED | FAILED.
    VALIDATED is never written on ordinary paths.
    """

    def __init__(
        self,
        teaching_uow_factory: TeachingUnitOfWorkFactory,
        ai_uow_factory: AIUnitOfWorkFactory,
        content_uow_factory: ContentUnitOfWorkFactory,
        worksheet_capability: GenerateWorksheetCapability,
        create_ai_content_for_review: CreateAIGeneratedContentForReviewService,
        *,
        provider_id: str,
        model_id: str,
        lease_seconds: int = DEFAULT_GENERATION_LEASE_SECONDS,
        clock: UtcNow | None = None,
    ) -> None:
        self._teaching_uow_factory = teaching_uow_factory
        self._ai_uow_factory = ai_uow_factory
        self._content_uow_factory = content_uow_factory
        self._worksheet_capability = worksheet_capability
        self._create_ai_content_for_review = create_ai_content_for_review
        self._provider_id = provider_id
        self._model_id = model_id
        self._lease_seconds = lease_seconds
        self._clock = clock if clock is not None else utc_now

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
        claim_at = now if now is not None else self._clock()
        key_hash = hash_idempotency_key(idempotency_key)
        lease_expires_at = claim_at + timedelta(seconds=self._lease_seconds)

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

        run_id = self._claim_or_resolve_run(
            execution_tenant_id,
            principal_id,
            work_id=work_id,
            expected_aggregate_revision=expected_aggregate_revision,
            key_hash=key_hash,
            fingerprint=fingerprint,
            lease_expires_at=lease_expires_at,
            now=claim_at,
        )
        if isinstance(run_id, GenerateTeachingWorkResult):
            return run_id

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
            failure_at = self._clock()
            self._mark_failed(
                execution_tenant_id,
                run_id,
                failure_code="educational_quality_failed",
                educational_quality_summary=exc.draft.educational_quality_result.as_summary(),
                provider_metadata=exc.draft.provider_metadata,
                now=failure_at,
            )
            raise EducationalQualityFailedError(
                educational_quality=_quality_view(exc.draft.educational_quality_result)
            ) from exc
        except ModelProviderUnavailable as exc:
            failure_at = self._clock()
            self._mark_failed(
                execution_tenant_id,
                run_id,
                failure_code="model_provider_unavailable",
                now=failure_at,
            )
            raise ModelProviderUnavailableError("model provider unavailable") from exc
        except ModelOutputInvalid as exc:
            failure_at = self._clock()
            self._mark_failed(
                execution_tenant_id,
                run_id,
                failure_code="model_output_invalid",
                now=failure_at,
            )
            raise ModelOutputInvalidError("model output invalid") from exc
        except ModelGenerationFailed as exc:
            failure_at = self._clock()
            self._mark_failed(
                execution_tenant_id,
                run_id,
                failure_code="model_generation_failed",
                now=failure_at,
            )
            raise ModelGenerationFailedError("model generation failed") from exc

        quality_summary = draft.educational_quality_result.as_summary()
        meta = draft.provider_metadata
        heartbeat_at = self._clock()

        with self._ai_uow_factory(execution_tenant_id) as ai_uow:
            locked = ai_uow.generation_runs.get_for_update(run_id)
            if locked is None:
                raise TeachingWorkNotFound("GenerationRun was not found")
            if locked.status is GenerationRunStatus.SUCCEEDED:
                return self._result_from_run(work_id, locked)
            # Stay RUNNING; refresh provider metadata and lease from current time.
            refreshed = replace(
                locked,
                provider_id=meta.provider_id,
                model_id=meta.model_id,
                provider_response_id=meta.provider_response_id,
                input_tokens=meta.input_tokens,
                output_tokens=meta.output_tokens,
                total_tokens=meta.total_tokens,
                educational_quality_summary=quality_summary,
                lease_expires_at=heartbeat_at + timedelta(seconds=self._lease_seconds),
                updated_at=heartbeat_at,
            )
            if not ai_uow.generation_runs.update(
                refreshed, expected_revision=locked.aggregate_revision
            ):
                raise WorkGenerationInProgress("work generation concurrency conflict")
            ai_uow.commit()
            run_revision = refreshed.aggregate_revision

        provenance = AIGenerationProvenanceV1(
            generation_run_ref=ResourceRef(
                GENERATION_RUN_RESOURCE_TYPE, run_id.value, run_revision
            ),
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
        materialization_at = self._clock()
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
                now=materialization_at,
            )
        except AIGenerationForbidden as exc:
            failure_at = self._clock()
            self._mark_failed(
                execution_tenant_id,
                run_id,
                failure_code="content_materialization_failed",
                educational_quality_summary=quality_summary,
                provider_metadata=meta,
                now=failure_at,
            )
            raise TeachingWorkForbidden("AI content materialization is forbidden") from exc
        except ContentApplicationError as exc:
            failure_at = self._clock()
            self._mark_failed(
                execution_tenant_id,
                run_id,
                failure_code="content_materialization_failed",
                educational_quality_summary=quality_summary,
                provider_metadata=meta,
                now=failure_at,
            )
            raise ContentMaterializationFailedError(
                "content materialization failed"
            ) from exc
        except Exception as exc:
            failure_at = self._clock()
            self._mark_failed(
                execution_tenant_id,
                run_id,
                failure_code="content_materialization_failed",
                educational_quality_summary=quality_summary,
                provider_metadata=meta,
                now=failure_at,
            )
            raise ContentMaterializationFailedError(
                "content materialization failed"
            ) from exc

        finalize_at = self._clock()
        return self._finalize_succeeded(
            execution_tenant_id,
            work_id,
            run_id,
            materialization=materialization,
            quality_summary=quality_summary,
            now=finalize_at,
        )

    def _claim_or_resolve_run(
        self,
        execution_tenant_id: UUID,
        principal_id: UUID,
        *,
        work_id: WorkId,
        expected_aggregate_revision: AggregateRevision,
        key_hash: str,
        fingerprint: str,
        lease_expires_at: datetime,
        now: datetime,
        _attempt: int = 0,
    ) -> GenerationRunId | GenerateTeachingWorkResult:
        """Insert RUNNING under work/idempotency fences; resolve conflicts without model call."""
        if _attempt > 2:
            raise WorkGenerationInProgress("work generation concurrency conflict")
        with self._ai_uow_factory(execution_tenant_id) as ai_uow:
            by_key = ai_uow.generation_runs.get_by_idempotency_key(
                principal_id=principal_id,
                idempotency_key_sha256=key_hash,
            )
            if by_key is not None:
                resolved = self._resolve_existing_run(
                    execution_tenant_id,
                    principal_id,
                    work_id=work_id,
                    run=by_key,
                    fingerprint=fingerprint,
                    key_hash=key_hash,
                    lease_expires_at=lease_expires_at,
                    now=now,
                    same_key=True,
                )
                if resolved is not None:
                    return resolved

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
                created_at=now,
                updated_at=now,
                completed_at=None,
                lease_expires_at=lease_expires_at,
            )
            try:
                ai_uow.generation_runs.insert(run)
                ai_uow.commit()
                return run.generation_run_id
            except GenerationRunConflict:
                ai_uow.rollback()

        with self._ai_uow_factory(execution_tenant_id) as ai_uow:
            by_key = ai_uow.generation_runs.get_by_idempotency_key(
                principal_id=principal_id,
                idempotency_key_sha256=key_hash,
            )
            winner = ai_uow.generation_runs.find_active_or_succeeded_for_work(
                work_resource_id=work_id.value,
            )
            conflict_run = by_key if by_key is not None else winner
            if conflict_run is None:
                raise WorkGenerationInProgress("work generation concurrency conflict")
            resolved = self._resolve_existing_run(
                execution_tenant_id,
                principal_id,
                work_id=work_id,
                run=conflict_run,
                fingerprint=fingerprint,
                key_hash=key_hash,
                lease_expires_at=lease_expires_at,
                now=now,
                same_key=(
                    by_key is not None
                    and by_key.idempotency_key_sha256 == key_hash
                ),
            )
            if resolved is not None:
                return resolved

        # Stale fence released — retry claim once.
        return self._claim_or_resolve_run(
            execution_tenant_id,
            principal_id,
            work_id=work_id,
            expected_aggregate_revision=expected_aggregate_revision,
            key_hash=key_hash,
            fingerprint=fingerprint,
            lease_expires_at=lease_expires_at,
            now=now,
            _attempt=_attempt + 1,
        )

    def _resolve_existing_run(
        self,
        execution_tenant_id: UUID,
        principal_id: UUID,
        *,
        work_id: WorkId,
        run: GenerationRun,
        fingerprint: str,
        key_hash: str,
        lease_expires_at: datetime,
        now: datetime,
        same_key: bool,
    ) -> GenerationRunId | GenerateTeachingWorkResult | None:
        if same_key and run.request_fingerprint_sha256 != fingerprint:
            raise GenerationIdempotencyConflict(
                "Idempotency-Key was already used with a different request"
            )

        if run.status is GenerationRunStatus.SUCCEEDED:
            if same_key:
                return self._result_from_run(work_id, run)
            err = WorkGenerationAlreadyExists()
            err.existing_generation_run_id = run.generation_run_id
            err.existing_content_id = run.result_content_id
            err.existing_version_id = run.result_version_id
            raise err

        if run.status is GenerationRunStatus.FAILED:
            if same_key:
                self._raise_failure_from_run(run)
            return None

        if run.status is GenerationRunStatus.VALIDATED:
            # Compatibility-only status; treat as in-progress / reclaimable.
            pass

        if run.status in (
            GenerationRunStatus.RUNNING,
            GenerationRunStatus.VALIDATED,
        ):
            if _lease_fresh(run, now=now):
                raise WorkGenerationInProgress("work generation is in progress")

            # Stale RUNNING: reconcile Content first (crash after materialize).
            reconciled = self._reconcile_stale_with_content(
                execution_tenant_id, work_id, run, now=now
            )
            if reconciled is not None:
                return reconciled

            if same_key:
                reclaimed = self._reclaim_lease(
                    execution_tenant_id,
                    run.generation_run_id,
                    lease_expires_at=lease_expires_at,
                    now=now,
                )
                if reclaimed:
                    return run.generation_run_id
                raise WorkGenerationInProgress("work generation is in progress")

            # Different key: fail-stale to release work fence, then allow retry.
            self._mark_failed(
                execution_tenant_id,
                run.generation_run_id,
                failure_code="generation_lease_expired",
                now=now,
            )
            return None

        raise WorkGenerationInProgress("work generation is in progress")

    def _reconcile_stale_with_content(
        self,
        execution_tenant_id: UUID,
        work_id: WorkId,
        run: GenerationRun,
        *,
        now: datetime,
    ) -> GenerateTeachingWorkResult | None:
        with self._content_uow_factory(execution_tenant_id) as content_uow:
            version = find_content_version_by_generation_run_id(
                content_uow, run.generation_run_id.value
            )
            if version is None:
                return None
            content = content_uow.contents.get(version.content_id)
            materialization = CreateAIGeneratedContentForReviewResult(
                content_id=version.content_id,
                version_id=version.version_id,
                content_type=(
                    WORKSHEET_CONTENT_TYPE
                    if content is None
                    else str(content.content_type)
                ),
                title="" if content is None else content.title,
                stewardship_state="IN_REVIEW",
                aggregate_revision=(
                    ContentAggregateRevision(0)
                    if content is None
                    else content.aggregate_revision
                ),
            )
        return self._finalize_succeeded(
            execution_tenant_id,
            work_id,
            run.generation_run_id,
            materialization=materialization,
            quality_summary=run.educational_quality_summary,
            now=now,
        )

    def _reclaim_lease(
        self,
        execution_tenant_id: UUID,
        run_id: GenerationRunId,
        *,
        lease_expires_at: datetime,
        now: datetime,
    ) -> bool:
        with self._ai_uow_factory(execution_tenant_id) as ai_uow:
            locked = ai_uow.generation_runs.get_for_update(run_id)
            if locked is None:
                return False
            if locked.status is GenerationRunStatus.SUCCEEDED:
                return False
            if locked.status is GenerationRunStatus.FAILED:
                return False
            if _lease_fresh(locked, now=now):
                return False
            reclaimed = replace(
                locked,
                status=GenerationRunStatus.RUNNING,
                lease_expires_at=lease_expires_at,
                updated_at=now,
                aggregate_revision=locked.aggregate_revision + 1,
            )
            ok = ai_uow.generation_runs.update(
                reclaimed, expected_revision=locked.aggregate_revision
            )
            if ok:
                ai_uow.commit()
            return ok

    def _finalize_succeeded(
        self,
        execution_tenant_id: UUID,
        work_id: WorkId,
        run_id: GenerationRunId,
        *,
        materialization: CreateAIGeneratedContentForReviewResult,
        quality_summary: Mapping[str, object] | None,
        now: datetime,
    ) -> GenerateTeachingWorkResult:
        with self._ai_uow_factory(execution_tenant_id) as ai_uow:
            locked = ai_uow.generation_runs.get_for_update(run_id)
            if locked is None:
                raise TeachingWorkNotFound("GenerationRun was not found")
            if locked.status is GenerationRunStatus.SUCCEEDED:
                return self._result_from_run(
                    work_id, locked, materialization=materialization
                )
            succeeded = replace(
                locked,
                status=GenerationRunStatus.SUCCEEDED,
                result_content_id=materialization.content_id.value,
                result_version_id=materialization.version_id.value,
                result_content_revision=int(materialization.aggregate_revision),
                educational_quality_summary=(
                    quality_summary
                    if quality_summary is not None
                    else locked.educational_quality_summary
                ),
                lease_expires_at=None,
                aggregate_revision=locked.aggregate_revision + 1,
                updated_at=now,
                completed_at=now,
            )
            if not ai_uow.generation_runs.update(
                succeeded, expected_revision=locked.aggregate_revision
            ):
                # Concurrent finalizer won — re-read.
                current = ai_uow.generation_runs.get(run_id)
                if current is not None and current.status is GenerationRunStatus.SUCCEEDED:
                    return self._result_from_run(
                        work_id, current, materialization=materialization
                    )
                raise WorkGenerationInProgress("work generation concurrency conflict")
            ai_uow.commit()
            return self._result_from_run(
                work_id, succeeded, materialization=materialization
            )

    def _raise_failure_from_run(self, run: GenerationRun) -> None:
        code = run.failure_code or "model_generation_failed"
        if code == "educational_quality_failed":
            raise EducationalQualityFailedError(
                educational_quality=_quality_view_from_summary(
                    run.educational_quality_summary
                )
            )
        if code == "model_provider_unavailable":
            raise ModelProviderUnavailableError("model provider unavailable")
        if code == "model_output_invalid":
            raise ModelOutputInvalidError("model output invalid")
        if code == "content_materialization_failed":
            raise ContentMaterializationFailedError("content materialization failed")
        if code == "generation_lease_expired":
            raise WorkGenerationInProgress(
                "prior generation lease expired; retry with a new Idempotency-Key"
            )
        raise ModelGenerationFailedError(code)

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
            if locked.status in (
                GenerationRunStatus.SUCCEEDED,
                GenerationRunStatus.FAILED,
            ):
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
                lease_expires_at=None,
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

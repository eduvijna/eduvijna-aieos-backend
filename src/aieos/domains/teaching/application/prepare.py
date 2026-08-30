"""Teaching Work → preparation kit orchestration (TOS-DEV04-I06/I06R1).

Compose I04 generation + I05 Educational Quality + I03 atomic materialization
with GenerationRun fences, idempotency, Content-first crash recovery, and
execution-epoch ownership isolation after the provider call.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Mapping
from uuid import UUID

from aieos.domains.content.application.ai_preparation_for_review import (
    CreateAIPreparationArtifactsForReviewCommand,
    CreateAIPreparationArtifactsForReviewResult,
    CreateAIPreparationArtifactsForReviewService,
    PreparationProvenanceContext,
)
from aieos.domains.content.application.audit import ai_materialization_audit_provenance
from aieos.domains.content.application.errors import (
    AIGenerationForbidden,
    AIPreparationArtifactsAlreadyMaterialized,
    ContentApplicationError,
)
from aieos.domains.content.application.ports import ContentUnitOfWorkFactory
from aieos.domains.content.application.preparation_recovery import (
    ExactSixPreparationRecovery,
    PreparationBindingRecoveryStatus,
    inspect_preparation_generation_bindings,
)
from aieos.domains.education.application.generate_preparation_kit import (
    GeneratePreparationKitCapability,
)
from aieos.domains.education.application.models import PreparationKitGenerationInput
from aieos.domains.education.application.preparation_artifacts import (
    PreparationArtifactBuildFailed,
)
from aieos.domains.education.schema import PREPARATION_ARTIFACT_KINDS
from aieos.domains.teaching.application.errors import (
    ContentMaterializationFailedError,
    EducationalQualityFailedError,
    GenerationIdempotencyConflict,
    ModelGenerationFailedError,
    ModelOutputInvalidError,
    ModelProviderUnavailableError,
    PreparationRecoveryInvariantError,
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
    ModelAdapterContractFailed,
    ModelGenerationFailed,
    ModelOutputIncomplete,
    ModelOutputInvalid,
    ModelOutputMissing,
    ModelProviderUnavailable,
    ModelRequestRejected,
)
from aieos.platform.capabilities.models import (
    CAPABILITY_EDUCATION_GENERATE_PREPARATION_KIT,
)
from aieos.platform.education.preparation_quality_baseline import (
    evaluate_preparation_educational_quality_v1,
)
from aieos.platform.education.quality_baseline import (
    EducationalQualityResult,
    EducationalQualityStatus,
    educational_quality_from_summary,
)
from aieos.platform.events.models import MutationEventContext
from aieos.platform.idempotency.hashing import fingerprint_material, hash_idempotency_key
from aieos.platform.resources import ResourceRef

WORK_RESOURCE_TYPE = "teaching.work"
GENERATION_RUN_RESOURCE_TYPE = "ai.generation_run"


@dataclass(frozen=True, slots=True)
class PreparationGenerationClaim:
    """In-memory execution-epoch ownership token (not persisted, not API-exposed)."""

    generation_run_id: GenerationRunId
    claimed_aggregate_revision: int


@dataclass(frozen=True, slots=True)
class EducationalQualityView:
    status: str
    checks: tuple[dict[str, object], ...]


@dataclass(frozen=True, slots=True)
class PreparationArtifactView:
    artifact_kind: str
    content_id: UUID
    version_id: UUID
    content_type: str
    title: str
    stewardship_state: str
    aggregate_revision: int


@dataclass(frozen=True, slots=True)
class PrepareTeachingWorkResult:
    work_id: WorkId
    work_revision: int
    generation_run_id: GenerationRunId
    artifacts: tuple[PreparationArtifactView, ...]
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


def _require_pass_quality_summary(
    summary: Mapping[str, object] | None,
) -> EducationalQualityView:
    parsed = educational_quality_from_summary(summary)
    if parsed is None or parsed.status is not EducationalQualityStatus.PASS:
        raise PreparationRecoveryInvariantError(
            "SUCCEEDED preparation lacks a valid PASS educational quality summary"
        )
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


def _views_from_materialization(
    materialization: CreateAIPreparationArtifactsForReviewResult,
) -> tuple[PreparationArtifactView, ...]:
    by_kind = {item.artifact_kind: item for item in materialization.artifacts}
    return tuple(
        PreparationArtifactView(
            artifact_kind=kind,
            content_id=by_kind[kind].content_id.value,
            version_id=by_kind[kind].version_id.value,
            content_type=by_kind[kind].content_type,
            title=by_kind[kind].title,
            stewardship_state=by_kind[kind].stewardship_state,
            aggregate_revision=int(by_kind[kind].aggregate_revision),
        )
        for kind in PREPARATION_ARTIFACT_KINDS
    )


def _views_from_recovery(
    recovery: ExactSixPreparationRecovery,
) -> tuple[PreparationArtifactView, ...]:
    return tuple(
        PreparationArtifactView(
            artifact_kind=item.artifact_kind,
            content_id=item.content_id,
            version_id=item.version_id,
            content_type=item.content_type,
            title=item.title,
            stewardship_state=item.stewardship_state,
            aggregate_revision=item.aggregate_revision,
        )
        for item in recovery.artifacts
    )


class PrepareTeachingWorkService:
    """Orchestrate preparation GenerationRun → I04 → I05 → I03 → SUCCEEDED."""

    def __init__(
        self,
        teaching_uow_factory: TeachingUnitOfWorkFactory,
        ai_uow_factory: AIUnitOfWorkFactory,
        content_uow_factory: ContentUnitOfWorkFactory,
        preparation_capability: GeneratePreparationKitCapability,
        create_preparation_for_review: CreateAIPreparationArtifactsForReviewService,
        *,
        provider_id: str,
        model_id: str,
        lease_seconds: int = DEFAULT_GENERATION_LEASE_SECONDS,
        clock: UtcNow | None = None,
    ) -> None:
        self._teaching_uow_factory = teaching_uow_factory
        self._ai_uow_factory = ai_uow_factory
        self._content_uow_factory = content_uow_factory
        self._preparation_capability = preparation_capability
        self._create_preparation_for_review = create_preparation_for_review
        self._provider_id = provider_id
        self._model_id = model_id
        self._lease_seconds = lease_seconds
        self._clock = clock if clock is not None else utc_now

    def prepare(
        self,
        execution_tenant_id: UUID,
        principal_id: UUID,
        *,
        work_id: WorkId,
        expected_aggregate_revision: AggregateRevision,
        idempotency_key: str,
        event_context: MutationEventContext,
        now: datetime | None = None,
    ) -> PrepareTeachingWorkResult:
        claim_at = now if now is not None else self._clock()
        key_hash = hash_idempotency_key(idempotency_key)
        lease_expires_at = claim_at + timedelta(seconds=self._lease_seconds)
        work_revision = int(expected_aggregate_revision)

        with self._teaching_uow_factory(execution_tenant_id) as teaching_uow:
            work = teaching_uow.works.get(work_id)
            if work is None or work.tenant_id != execution_tenant_id:
                raise TeachingWorkNotFound("Teaching Work was not found")
            if work.teacher_principal_id != principal_id:
                raise TeachingWorkForbidden(
                    "Teaching Work is owned by a different teacher"
                )
            if work.aggregate_revision != expected_aggregate_revision:
                raise WorkGenerationRevisionConflict(
                    "If-Match does not match the current Work revision"
                )
            work_snapshot = work

        fingerprint = _generation_fingerprint(
            work_id=work_id,
            work_revision=work_revision,
            capability_id=CAPABILITY_EDUCATION_GENERATE_PREPARATION_KIT,
            provider_id=self._provider_id,
            model_id=self._model_id,
        )

        claimed = self._claim_or_resolve_run(
            execution_tenant_id,
            principal_id,
            work_id=work_id,
            expected_aggregate_revision=expected_aggregate_revision,
            key_hash=key_hash,
            fingerprint=fingerprint,
            lease_expires_at=lease_expires_at,
            now=claim_at,
        )
        if isinstance(claimed, PrepareTeachingWorkResult):
            return claimed

        generation_input = PreparationKitGenerationInput(
            work_ref=ResourceRef(
                WORK_RESOURCE_TYPE,
                work_id.value,
                work_revision,
            ),
            goal_text=work_snapshot.goal_text,
            class_label=work_snapshot.class_label,
            subject=work_snapshot.subject,
            topic=work_snapshot.topic,
            target_date=work_snapshot.target_date,
            locale=work_snapshot.locale,
        )

        try:
            draft = self._preparation_capability.execute(generation_input)
        except PreparationArtifactBuildFailed as exc:
            owned = self._mark_failed_if_owned(
                execution_tenant_id,
                work_id,
                claimed,
                failure_code="preparation_artifact_build_failed",
            )
            if isinstance(owned, PrepareTeachingWorkResult):
                return owned
            raise ModelOutputInvalidError("model output invalid") from exc
        except ModelProviderUnavailable as exc:
            owned = self._mark_failed_if_owned(
                execution_tenant_id,
                work_id,
                claimed,
                failure_code="model_provider_unavailable",
            )
            if isinstance(owned, PrepareTeachingWorkResult):
                return owned
            raise ModelProviderUnavailableError("model provider unavailable") from exc
        except ModelOutputInvalid as exc:
            owned = self._mark_failed_if_owned(
                execution_tenant_id,
                work_id,
                claimed,
                failure_code="model_output_invalid",
            )
            if isinstance(owned, PrepareTeachingWorkResult):
                return owned
            raise ModelOutputInvalidError("model output invalid") from exc
        except ModelOutputIncomplete as exc:
            owned = self._mark_failed_if_owned(
                execution_tenant_id,
                work_id,
                claimed,
                failure_code="model_output_incomplete",
            )
            if isinstance(owned, PrepareTeachingWorkResult):
                return owned
            raise ModelOutputInvalidError("model output invalid") from exc
        except ModelOutputMissing as exc:
            owned = self._mark_failed_if_owned(
                execution_tenant_id,
                work_id,
                claimed,
                failure_code="model_output_missing",
            )
            if isinstance(owned, PrepareTeachingWorkResult):
                return owned
            raise ModelOutputInvalidError("model output invalid") from exc
        except ModelRequestRejected as exc:
            owned = self._mark_failed_if_owned(
                execution_tenant_id,
                work_id,
                claimed,
                failure_code="model_request_rejected",
            )
            if isinstance(owned, PrepareTeachingWorkResult):
                return owned
            raise ModelGenerationFailedError("model generation failed") from exc
        except ModelAdapterContractFailed as exc:
            owned = self._mark_failed_if_owned(
                execution_tenant_id,
                work_id,
                claimed,
                failure_code="model_adapter_contract_failed",
            )
            if isinstance(owned, PrepareTeachingWorkResult):
                return owned
            raise ModelGenerationFailedError("model generation failed") from exc
        except ModelGenerationFailed as exc:
            owned = self._mark_failed_if_owned(
                execution_tenant_id,
                work_id,
                claimed,
                failure_code="model_generation_failed",
            )
            if isinstance(owned, PrepareTeachingWorkResult):
                return owned
            raise ModelGenerationFailedError("model generation failed") from exc

        quality = evaluate_preparation_educational_quality_v1(draft.artifacts)
        meta = draft.provider_metadata
        if quality.status is not EducationalQualityStatus.PASS:
            owned_fail = self._mark_failed_if_owned(
                execution_tenant_id,
                work_id,
                claimed,
                failure_code="educational_quality_failed",
                educational_quality_summary=quality.as_summary(),
                provider_metadata=meta,
            )
            if isinstance(owned_fail, PrepareTeachingWorkResult):
                return owned_fail
            raise EducationalQualityFailedError(
                educational_quality=_quality_view(quality)
            )

        quality_summary = quality.as_summary()
        ownership = self._persist_provider_quality_if_owned(
            execution_tenant_id,
            work_id,
            claimed,
            fingerprint=fingerprint,
            provider_metadata=meta,
            quality_summary=quality_summary,
        )
        if isinstance(ownership, PrepareTeachingWorkResult):
            return ownership
        post_quality_revision = ownership
        post_quality_claim = PreparationGenerationClaim(
            generation_run_id=claimed.generation_run_id,
            claimed_aggregate_revision=post_quality_revision,
        )

        provenance = PreparationProvenanceContext(
            generation_run_ref=ResourceRef(
                GENERATION_RUN_RESOURCE_TYPE,
                claimed.generation_run_id.value,
                post_quality_revision,
            ),
            prompt_execution_ref=None,
            provider_id=meta.provider_id,
            model_id=meta.model_id,
            source_refs=(
                ResourceRef(WORK_RESOURCE_TYPE, work_id.value, work_revision),
            ),
            policy_refs=(),
            evaluation_refs=(),
            correlation_id=event_context.correlation_id,
        )
        command = CreateAIPreparationArtifactsForReviewCommand(
            lesson_plan=draft.artifacts.lesson_plan,
            worksheet=draft.artifacts.worksheet,
            quiz=draft.artifacts.quiz,
            homework=draft.artifacts.homework,
            answer_key=draft.artifacts.answer_key,
            teacher_notes=draft.artifacts.teacher_notes,
            locale=work_snapshot.locale,
            teacher_summary=draft.preparation_kit.teacher_summary,
            provenance=provenance,
        )

        try:
            materialization = self._create_preparation_for_review.create(
                execution_tenant_id,
                principal_id,
                command,
                event_context=event_context,
                audit_provenance=ai_materialization_audit_provenance(principal_id),
                now=self._clock(),
            )
        except AIPreparationArtifactsAlreadyMaterialized:
            finalize_at = self._clock()
            recovered = self._require_exact_six_recovery(
                execution_tenant_id, claimed.generation_run_id
            )
            return self._finalize_succeeded(
                execution_tenant_id,
                work_id,
                claimed.generation_run_id,
                artifacts=_views_from_recovery(recovered),
                quality_summary=quality_summary,
                expected_revision=post_quality_revision,
                now=finalize_at,
            )
        except (AIGenerationForbidden, ContentApplicationError, Exception) as exc:
            finalize_at = self._clock()
            inspection = self._inspect_bindings(
                execution_tenant_id, claimed.generation_run_id
            )
            if inspection.status is PreparationBindingRecoveryStatus.EXACT_SIX:
                assert inspection.recovery is not None
                return self._finalize_succeeded(
                    execution_tenant_id,
                    work_id,
                    claimed.generation_run_id,
                    artifacts=_views_from_recovery(inspection.recovery),
                    quality_summary=quality_summary,
                    expected_revision=post_quality_revision,
                    now=finalize_at,
                )
            if inspection.status is PreparationBindingRecoveryStatus.INVALID:
                raise PreparationRecoveryInvariantError(
                    "preparation Content bindings are partial or corrupt"
                ) from exc
            owned_fail = self._mark_failed_if_owned(
                execution_tenant_id,
                work_id,
                post_quality_claim,
                failure_code="content_materialization_failed",
                educational_quality_summary=quality_summary,
                provider_metadata=meta,
            )
            if isinstance(owned_fail, PrepareTeachingWorkResult):
                return owned_fail
            raise ContentMaterializationFailedError(
                "content materialization failed"
            ) from exc

        finalize_at = self._clock()
        return self._finalize_succeeded(
            execution_tenant_id,
            work_id,
            claimed.generation_run_id,
            artifacts=_views_from_materialization(materialization),
            quality_summary=quality_summary,
            expected_revision=post_quality_revision,
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
    ) -> PreparationGenerationClaim | PrepareTeachingWorkResult:
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
                    requested_work_revision=int(expected_aggregate_revision),
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
                capability_id=CAPABILITY_EDUCATION_GENERATE_PREPARATION_KIT,
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
                return PreparationGenerationClaim(
                    generation_run_id=run.generation_run_id,
                    claimed_aggregate_revision=0,
                )
            except GenerationRunConflict:
                ai_uow.rollback()

        with self._ai_uow_factory(execution_tenant_id) as ai_uow:
            by_key = ai_uow.generation_runs.get_by_idempotency_key(
                principal_id=principal_id,
                idempotency_key_sha256=key_hash,
            )
            outcome = ai_uow.generation_runs.find_outcome_for_work_revision_capability(
                work_resource_id=work_id.value,
                work_resource_revision=int(expected_aggregate_revision),
                capability_id=CAPABILITY_EDUCATION_GENERATE_PREPARATION_KIT,
            )
            running = ai_uow.generation_runs.find_running_for_work_capability(
                work_resource_id=work_id.value,
                capability_id=CAPABILITY_EDUCATION_GENERATE_PREPARATION_KIT,
            )
            conflict_run = by_key if by_key is not None else (
                outcome if outcome is not None else running
            )
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
                    by_key is not None and by_key.idempotency_key_sha256 == key_hash
                ),
                requested_work_revision=int(expected_aggregate_revision),
            )
            if resolved is not None:
                return resolved

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
        requested_work_revision: int,
    ) -> PreparationGenerationClaim | PrepareTeachingWorkResult | None:
        if same_key and run.request_fingerprint_sha256 != fingerprint:
            raise GenerationIdempotencyConflict(
                "Idempotency-Key was already used with a different request"
            )

        cross_revision = run.work_resource_revision != requested_work_revision

        if run.status is GenerationRunStatus.SUCCEEDED:
            if cross_revision:
                return None
            if same_key:
                return self._result_from_succeeded_run(
                    execution_tenant_id, work_id, run
                )
            err = WorkGenerationAlreadyExists()
            err.existing_generation_run_id = run.generation_run_id
            err.existing_content_id = None
            err.existing_version_id = None
            raise err

        if run.status is GenerationRunStatus.FAILED:
            if same_key:
                self._raise_failure_from_run(run)
            return None

        if run.status in (
            GenerationRunStatus.RUNNING,
            GenerationRunStatus.VALIDATED,
        ):
            # Content-first: committed exact-six wins even with a fresh lease.
            inspection = self._inspect_bindings_for_run(execution_tenant_id, run)
            if inspection.status is PreparationBindingRecoveryStatus.EXACT_SIX:
                assert inspection.recovery is not None
                finalized = self._finalize_succeeded(
                    execution_tenant_id,
                    work_id,
                    run.generation_run_id,
                    artifacts=_views_from_recovery(inspection.recovery),
                    quality_summary=run.educational_quality_summary,
                    expected_revision=None,
                    now=now,
                )
                if cross_revision:
                    return None
                if same_key:
                    return finalized
                err = WorkGenerationAlreadyExists()
                err.existing_generation_run_id = run.generation_run_id
                err.existing_content_id = None
                err.existing_version_id = None
                raise err

            if inspection.status is PreparationBindingRecoveryStatus.INVALID:
                raise PreparationRecoveryInvariantError(
                    "preparation Content bindings are partial or corrupt"
                )

            # ZERO bindings
            if _lease_fresh(run, now=now):
                raise WorkGenerationInProgress("work generation is in progress")

            if same_key:
                if cross_revision:
                    self._mark_failed(
                        execution_tenant_id,
                        run.generation_run_id,
                        failure_code="generation_lease_expired",
                        now=now,
                    )
                    return None
                new_revision = self._reclaim_lease(
                    execution_tenant_id,
                    run.generation_run_id,
                    lease_expires_at=lease_expires_at,
                    now=now,
                )
                if new_revision is not None:
                    return PreparationGenerationClaim(
                        generation_run_id=run.generation_run_id,
                        claimed_aggregate_revision=new_revision,
                    )
                raise WorkGenerationInProgress("work generation is in progress")

            self._mark_failed(
                execution_tenant_id,
                run.generation_run_id,
                failure_code="generation_lease_expired",
                now=now,
            )
            return None

        raise WorkGenerationInProgress("work generation is in progress")

    def _inspect_bindings(
        self,
        execution_tenant_id: UUID,
        run_id: GenerationRunId,
    ):
        with self._ai_uow_factory(execution_tenant_id) as ai_uow:
            run = ai_uow.generation_runs.get(run_id)
            if run is None:
                raise TeachingWorkNotFound("GenerationRun was not found")
        return self._inspect_bindings_for_run(execution_tenant_id, run)

    def _inspect_bindings_for_run(
        self,
        execution_tenant_id: UUID,
        run: GenerationRun,
    ):
        with self._content_uow_factory(execution_tenant_id) as content_uow:
            return inspect_preparation_generation_bindings(content_uow, run)

    def _require_exact_six_recovery(
        self,
        execution_tenant_id: UUID,
        run_id: GenerationRunId,
    ) -> ExactSixPreparationRecovery:
        inspection = self._inspect_bindings(execution_tenant_id, run_id)
        if (
            inspection.status is not PreparationBindingRecoveryStatus.EXACT_SIX
            or inspection.recovery is None
        ):
            raise PreparationRecoveryInvariantError(
                "expected exact six valid preparation Content bindings"
            )
        return inspection.recovery

    def _reclaim_lease(
        self,
        execution_tenant_id: UUID,
        run_id: GenerationRunId,
        *,
        lease_expires_at: datetime,
        now: datetime,
    ) -> int | None:
        """Reclaim a stale RUNNING lease. Returns the new aggregate revision."""
        with self._ai_uow_factory(execution_tenant_id) as ai_uow:
            locked = ai_uow.generation_runs.get_for_update(run_id)
            if locked is None:
                return None
            if locked.status is GenerationRunStatus.SUCCEEDED:
                return None
            if locked.status is GenerationRunStatus.FAILED:
                return None
            if _lease_fresh(locked, now=now):
                return None
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
                return reclaimed.aggregate_revision
            return None

    def _persist_provider_quality_if_owned(
        self,
        execution_tenant_id: UUID,
        work_id: WorkId,
        claim: PreparationGenerationClaim,
        *,
        fingerprint: str,
        provider_metadata: object,
        quality_summary: Mapping[str, object],
    ) -> int | PrepareTeachingWorkResult:
        """Persist provider metadata + PASS quality only while claim owns RUNNING.

        Returns the new aggregate revision on success, or an authoritative result
        when the claim was superseded by SUCCEEDED / lost ownership resolved.
        """
        heartbeat_at = self._clock()
        meta = provider_metadata
        resolve_after: GenerationRun | None = None
        with self._ai_uow_factory(execution_tenant_id) as ai_uow:
            locked = ai_uow.generation_runs.get_for_update(claim.generation_run_id)
            if locked is None:
                raise TeachingWorkNotFound("GenerationRun was not found")

            if locked.status is GenerationRunStatus.SUCCEEDED:
                resolve_after = locked
            elif locked.status is GenerationRunStatus.FAILED:
                self._raise_failure_from_run(locked)
            else:
                owns = (
                    locked.status is GenerationRunStatus.RUNNING
                    and locked.aggregate_revision == claim.claimed_aggregate_revision
                    and locked.request_fingerprint_sha256 == fingerprint
                )
                if owns:
                    refreshed = replace(
                        locked,
                        provider_id=getattr(meta, "provider_id", locked.provider_id),
                        model_id=getattr(meta, "model_id", locked.model_id),
                        provider_response_id=getattr(
                            meta, "provider_response_id", locked.provider_response_id
                        ),
                        input_tokens=getattr(meta, "input_tokens", locked.input_tokens),
                        output_tokens=getattr(
                            meta, "output_tokens", locked.output_tokens
                        ),
                        total_tokens=getattr(meta, "total_tokens", locked.total_tokens),
                        educational_quality_summary=quality_summary,
                        lease_expires_at=heartbeat_at
                        + timedelta(seconds=self._lease_seconds),
                        updated_at=heartbeat_at,
                        aggregate_revision=locked.aggregate_revision + 1,
                    )
                    if ai_uow.generation_runs.update(
                        refreshed, expected_revision=locked.aggregate_revision
                    ):
                        ai_uow.commit()
                        return refreshed.aggregate_revision

        if resolve_after is not None:
            return self._result_from_succeeded_run(
                execution_tenant_id, work_id, resolve_after
            )
        return self._resolve_lost_ownership(
            execution_tenant_id, work_id, claim.generation_run_id
        )

    def _resolve_lost_ownership(
        self,
        execution_tenant_id: UUID,
        work_id: WorkId,
        run_id: GenerationRunId,
    ) -> PrepareTeachingWorkResult:
        with self._ai_uow_factory(execution_tenant_id) as ai_uow:
            current = ai_uow.generation_runs.get(run_id)
            if current is None:
                raise TeachingWorkNotFound("GenerationRun was not found")

        if current.status is GenerationRunStatus.SUCCEEDED:
            return self._result_from_succeeded_run(
                execution_tenant_id, work_id, current
            )
        if current.status is GenerationRunStatus.FAILED:
            self._raise_failure_from_run(current)

        inspection = self._inspect_bindings_for_run(execution_tenant_id, current)
        if inspection.status is PreparationBindingRecoveryStatus.EXACT_SIX:
            assert inspection.recovery is not None
            return self._finalize_succeeded(
                execution_tenant_id,
                work_id,
                run_id,
                artifacts=_views_from_recovery(inspection.recovery),
                quality_summary=current.educational_quality_summary,
                expected_revision=None,
                now=self._clock(),
            )
        if inspection.status is PreparationBindingRecoveryStatus.INVALID:
            raise PreparationRecoveryInvariantError(
                "preparation Content bindings are partial or corrupt"
            )
        raise WorkGenerationInProgress("work generation is in progress")

    def _finalize_succeeded(
        self,
        execution_tenant_id: UUID,
        work_id: WorkId,
        run_id: GenerationRunId,
        *,
        artifacts: tuple[PreparationArtifactView, ...],
        quality_summary: Mapping[str, object] | None,
        expected_revision: int | None,
        now: datetime,
    ) -> PrepareTeachingWorkResult:
        quality_view = _require_pass_quality_summary(quality_summary)
        succeeded_snapshot: GenerationRun | None = None
        lost = False
        concurrent_succeeded: GenerationRun | None = None
        result: PrepareTeachingWorkResult | None = None
        with self._ai_uow_factory(execution_tenant_id) as ai_uow:
            locked = ai_uow.generation_runs.get_for_update(run_id)
            if locked is None:
                raise TeachingWorkNotFound("GenerationRun was not found")
            if locked.status is GenerationRunStatus.SUCCEEDED:
                succeeded_snapshot = locked
            elif locked.status is GenerationRunStatus.FAILED:
                self._raise_failure_from_run(locked)
            elif (
                expected_revision is not None
                and locked.aggregate_revision != expected_revision
            ):
                lost = True
            elif locked.status is not GenerationRunStatus.RUNNING:
                raise WorkGenerationInProgress("work generation concurrency conflict")
            else:
                succeeded = replace(
                    locked,
                    status=GenerationRunStatus.SUCCEEDED,
                    result_content_id=None,
                    result_version_id=None,
                    result_content_revision=None,
                    educational_quality_summary=(
                        quality_summary
                        if quality_summary is not None
                        else locked.educational_quality_summary
                    ),
                    failure_code=None,
                    lease_expires_at=None,
                    aggregate_revision=locked.aggregate_revision + 1,
                    updated_at=now,
                    completed_at=now,
                )
                if not ai_uow.generation_runs.update(
                    succeeded, expected_revision=locked.aggregate_revision
                ):
                    current = ai_uow.generation_runs.get(run_id)
                    if (
                        current is not None
                        and current.status is GenerationRunStatus.SUCCEEDED
                    ):
                        concurrent_succeeded = current
                    else:
                        raise WorkGenerationInProgress(
                            "work generation concurrency conflict"
                        )
                else:
                    ai_uow.commit()
                    result = PrepareTeachingWorkResult(
                        work_id=work_id,
                        work_revision=succeeded.work_resource_revision,
                        generation_run_id=succeeded.generation_run_id,
                        artifacts=artifacts,
                        educational_quality=quality_view,
                    )

        if succeeded_snapshot is not None:
            return self._result_from_succeeded_run(
                execution_tenant_id, work_id, succeeded_snapshot
            )
        if concurrent_succeeded is not None:
            return self._result_from_succeeded_run(
                execution_tenant_id, work_id, concurrent_succeeded
            )
        if lost:
            return self._resolve_lost_ownership(execution_tenant_id, work_id, run_id)
        assert result is not None
        return result

    def _result_from_succeeded_run(
        self,
        execution_tenant_id: UUID,
        work_id: WorkId,
        run: GenerationRun,
    ) -> PrepareTeachingWorkResult:
        if run.status is not GenerationRunStatus.SUCCEEDED:
            raise WorkGenerationInProgress("generation result is not ready")
        quality_view = _require_pass_quality_summary(run.educational_quality_summary)
        recovery = self._require_exact_six_recovery(
            execution_tenant_id, run.generation_run_id
        )
        return PrepareTeachingWorkResult(
            work_id=work_id,
            work_revision=run.work_resource_revision,
            generation_run_id=run.generation_run_id,
            artifacts=_views_from_recovery(recovery),
            educational_quality=quality_view,
        )

    def _raise_failure_from_run(self, run: GenerationRun) -> None:
        code = run.failure_code or "model_generation_failed"
        if code == "educational_quality_failed":
            parsed = educational_quality_from_summary(run.educational_quality_summary)
            if parsed is None:
                raise PreparationRecoveryInvariantError(
                    "FAILED educational_quality_failed lacks a valid quality summary"
                )
            raise EducationalQualityFailedError(
                educational_quality=_quality_view(parsed)
            )
        if code == "model_provider_unavailable":
            raise ModelProviderUnavailableError("model provider unavailable")
        if code in (
            "model_output_invalid",
            "model_output_incomplete",
            "model_output_missing",
            "preparation_artifact_build_failed",
        ):
            raise ModelOutputInvalidError("model output invalid")
        if code in (
            "model_request_rejected",
            "model_adapter_contract_failed",
            "model_generation_failed",
        ):
            raise ModelGenerationFailedError("model generation failed")
        if code == "content_materialization_failed":
            raise ContentMaterializationFailedError("content materialization failed")
        if code == "generation_lease_expired":
            raise WorkGenerationInProgress(
                "prior generation lease expired; retry with a new Idempotency-Key"
            )
        raise ModelGenerationFailedError("model generation failed")

    def _mark_failed_if_owned(
        self,
        execution_tenant_id: UUID,
        work_id: WorkId,
        claim: PreparationGenerationClaim,
        *,
        failure_code: str,
        educational_quality_summary: Mapping[str, object] | None = None,
        provider_metadata: object | None = None,
    ) -> PrepareTeachingWorkResult | None:
        """Transition to FAILED only when claim still owns RUNNING revision.

        Returns None when this claim was marked FAILED.
        Returns PrepareTeachingWorkResult when a concurrent SUCCEEDED is observed.
        Raises for durable FAILED replay, in-progress, or corrupt Content.
        """
        now = self._clock()
        succeeded_snapshot: GenerationRun | None = None
        lost = False
        with self._ai_uow_factory(execution_tenant_id) as ai_uow:
            locked = ai_uow.generation_runs.get_for_update(claim.generation_run_id)
            if locked is None:
                raise TeachingWorkNotFound("GenerationRun was not found")

            if locked.status is GenerationRunStatus.SUCCEEDED:
                succeeded_snapshot = locked
            elif locked.status is GenerationRunStatus.FAILED:
                self._raise_failure_from_run(locked)
            else:
                owns = (
                    locked.status is GenerationRunStatus.RUNNING
                    and locked.aggregate_revision == claim.claimed_aggregate_revision
                )
                if not owns:
                    lost = True
                else:
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
                        output_tokens=getattr(
                            meta, "output_tokens", locked.output_tokens
                        ),
                        total_tokens=getattr(meta, "total_tokens", locked.total_tokens),
                        lease_expires_at=None,
                        aggregate_revision=locked.aggregate_revision + 1,
                        updated_at=now,
                        completed_at=now,
                    )
                    if not ai_uow.generation_runs.update(
                        failed, expected_revision=locked.aggregate_revision
                    ):
                        lost = True
                    else:
                        ai_uow.commit()
                        return None

        if succeeded_snapshot is not None:
            return self._result_from_succeeded_run(
                execution_tenant_id, work_id, succeeded_snapshot
            )
        return self._resolve_lost_ownership(
            execution_tenant_id, work_id, claim.generation_run_id
        )

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
        """Pre-provider fence release (stale different-key / cross-revision).

        Must not be used for post-provider failure paths — those use
        ``_mark_failed_if_owned``.
        """
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

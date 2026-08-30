"""Work artifact read projection via GenerationRun + Content queries."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from aieos.domains.content.application.ports import ContentUnitOfWorkFactory
from aieos.domains.content.application.preparation_recovery import (
    PreparationBindingRecoveryStatus,
    inspect_preparation_generation_bindings,
)
from aieos.domains.content.domain.identities import ContentId, ContentVersionId
from aieos.domains.education.schema import (
    PREPARATION_ARTIFACT_KINDS,
    WORKSHEET_CONTENT_TYPE,
)
from aieos.domains.teaching.application.errors import (
    PreparationRecoveryInvariantError,
    TeachingWorkForbidden,
    TeachingWorkNotFound,
)
from aieos.domains.teaching.application.generate import (
    EducationalQualityView,
    GeneratedArtifactView,
)
from aieos.domains.teaching.application.ports import TeachingUnitOfWorkFactory
from aieos.domains.teaching.domain.identities import WorkId
from aieos.platform.ai.application.ports import AIUnitOfWorkFactory
from aieos.platform.ai.domain.generation_run import GenerationRun, GenerationRunStatus
from aieos.platform.capabilities.models import (
    CAPABILITY_EDUCATION_GENERATE_PREPARATION_KIT,
)
from aieos.platform.education.quality_baseline import educational_quality_from_summary


@dataclass(frozen=True, slots=True)
class WorkArtifactItem:
    content_id: UUID
    version_id: UUID
    content_type: str
    title: str
    origin: str
    stewardship_state: str
    aggregate_revision: int
    educational_quality: EducationalQualityView | None
    artifact_kind: str | None = None
    generation_run_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class WorkArtifactsResult:
    work_id: WorkId
    items: tuple[WorkArtifactItem, ...]


def _quality_view_from_run(run: GenerationRun) -> EducationalQualityView | None:
    quality = educational_quality_from_summary(run.educational_quality_summary)
    if quality is None:
        return None
    return EducationalQualityView(
        status=quality.status.value,
        checks=tuple(
            {
                "code": c.code,
                "passed": c.passed,
                "explanation": c.explanation,
            }
            for c in quality.checks
        ),
    )


class ListTeachingWorkArtifactsService:
    def __init__(
        self,
        teaching_uow_factory: TeachingUnitOfWorkFactory,
        ai_uow_factory: AIUnitOfWorkFactory,
        content_uow_factory: ContentUnitOfWorkFactory,
    ) -> None:
        self._teaching_uow_factory = teaching_uow_factory
        self._ai_uow_factory = ai_uow_factory
        self._content_uow_factory = content_uow_factory

    def list(
        self,
        execution_tenant_id: UUID,
        principal_id: UUID,
        work_id: WorkId,
    ) -> WorkArtifactsResult:
        with self._teaching_uow_factory(execution_tenant_id) as teaching_uow:
            work = teaching_uow.works.get(work_id)
            if work is None or work.tenant_id != execution_tenant_id:
                raise TeachingWorkNotFound("Teaching Work was not found")
            if work.teacher_principal_id != principal_id:
                raise TeachingWorkForbidden(
                    "Teaching Work is owned by a different teacher"
                )

        with self._ai_uow_factory(execution_tenant_id) as ai_uow:
            runs = ai_uow.generation_runs.list_for_work(
                principal_id=principal_id,
                work_resource_id=work_id.value,
            )

        items: list[WorkArtifactItem] = []
        for run in runs:
            if run.status is not GenerationRunStatus.SUCCEEDED:
                continue
            if run.capability_id == CAPABILITY_EDUCATION_GENERATE_PREPARATION_KIT:
                items.extend(
                    self._preparation_items(execution_tenant_id, run)
                )
                continue
            if run.result_content_id is None or run.result_version_id is None:
                continue
            items.append(
                self._singular_item(execution_tenant_id, run)
            )
        return WorkArtifactsResult(work_id=work_id, items=tuple(items))

    def _preparation_items(
        self,
        execution_tenant_id: UUID,
        run: GenerationRun,
    ) -> list[WorkArtifactItem]:
        with self._content_uow_factory(execution_tenant_id) as content_uow:
            inspection = inspect_preparation_generation_bindings(content_uow, run)
        if inspection.status is PreparationBindingRecoveryStatus.INVALID:
            raise PreparationRecoveryInvariantError(
                "preparation Content bindings are partial or corrupt"
            )
        if (
            inspection.status is not PreparationBindingRecoveryStatus.EXACT_SIX
            or inspection.recovery is None
        ):
            raise PreparationRecoveryInvariantError(
                "SUCCEEDED preparation lacks exact six Content bindings"
            )
        quality_view = _quality_view_from_run(run)
        by_kind = {item.artifact_kind: item for item in inspection.recovery.artifacts}
        ordered: list[WorkArtifactItem] = []
        for kind in PREPARATION_ARTIFACT_KINDS:
            artifact = by_kind[kind]
            ordered.append(
                WorkArtifactItem(
                    content_id=artifact.content_id,
                    version_id=artifact.version_id,
                    content_type=artifact.content_type,
                    title=artifact.title,
                    origin="AI",
                    stewardship_state=artifact.stewardship_state,
                    aggregate_revision=artifact.aggregate_revision,
                    educational_quality=quality_view,
                    artifact_kind=kind,
                    generation_run_id=run.generation_run_id.value,
                )
            )
        return ordered

    def _singular_item(
        self,
        execution_tenant_id: UUID,
        run: GenerationRun,
    ) -> WorkArtifactItem:
        assert run.result_content_id is not None
        assert run.result_version_id is not None
        title = ""
        stewardship = "IN_REVIEW"
        content_type = WORKSHEET_CONTENT_TYPE
        aggregate_revision = run.result_content_revision or 0
        origin = "AI"
        with self._content_uow_factory(execution_tenant_id) as content_uow:
            content = content_uow.contents.get(ContentId(run.result_content_id))
            if content is not None:
                title = content.title
                stewardship = content.stewardship_state.value
                content_type = content.content_type.value
                aggregate_revision = int(content.aggregate_revision)
            version = content_uow.versions.get(ContentVersionId(run.result_version_id))
            if version is not None:
                origin = version.origin.value
        return WorkArtifactItem(
            content_id=run.result_content_id,
            version_id=run.result_version_id,
            content_type=content_type,
            title=title,
            origin=origin,
            stewardship_state=stewardship,
            aggregate_revision=aggregate_revision,
            educational_quality=_quality_view_from_run(run),
            artifact_kind=None,
            generation_run_id=run.generation_run_id.value,
        )


def artifact_view_from_content(
    *,
    content_id: UUID,
    version_id: UUID,
    content_type: str,
    title: str,
    stewardship_state: str,
    aggregate_revision: int,
) -> GeneratedArtifactView:
    return GeneratedArtifactView(
        content_id=content_id,
        version_id=version_id,
        content_type=content_type,
        title=title,
        stewardship_state=stewardship_state,
        aggregate_revision=aggregate_revision,
    )

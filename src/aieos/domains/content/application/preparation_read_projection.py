"""Durable SUCCEEDED preparation Work-artifact read projection (TOS-DEV04-I07R1).

Distinct from ``inspect_preparation_generation_bindings`` (I06 recovery):

* Recovery requires current_version_id == generation-bound version and IN_REVIEW.
* Read projection preserves historical GenerationRun-bound ContentVersions and
  projects *current* Content aggregate title / stewardship / revision.

Does not mutate GenerationRun or Content.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from uuid import UUID

from aieos.domains.content.application.ports import ContentUnitOfWork
from aieos.domains.content.domain.provenance import AIGenerationProvenanceV2
from aieos.domains.education.schema import (
    PREPARATION_ARTIFACT_KINDS,
    PREPARATION_CONTENT_TYPES,
)
from aieos.platform.ai.domain.generation_run import GenerationRun
from aieos.platform.capabilities.models import (
    CAPABILITY_EDUCATION_GENERATE_PREPARATION_KIT,
)
from aieos.platform.resources import ResourceRef

WORK_RESOURCE_TYPE = "teaching.work"
GENERATION_RUN_RESOURCE_TYPE = "ai.generation_run"

_KIND_TO_CONTENT_TYPE: dict[str, str] = dict(
    zip(PREPARATION_ARTIFACT_KINDS, PREPARATION_CONTENT_TYPES, strict=True)
)


class PreparationReadProjectionStatus(Enum):
    ZERO = "zero"
    EXACT_SIX = "exact_six"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class ProjectedPreparationArtifact:
    artifact_kind: str
    content_id: UUID
    version_id: UUID
    content_type: str
    title: str
    stewardship_state: str
    aggregate_revision: int


@dataclass(frozen=True, slots=True)
class ExactSixPreparationProjection:
    artifacts: tuple[ProjectedPreparationArtifact, ...]
    correlation_id: UUID
    provider_id: str
    model_id: str
    work_ref: ResourceRef
    generation_run_ref: ResourceRef


@dataclass(frozen=True, slots=True)
class PreparationReadProjection:
    status: PreparationReadProjectionStatus
    projection: ExactSixPreparationProjection | None = None
    detail: str | None = None


def project_preparation_generation_artifacts(
    content_uow: ContentUnitOfWork,
    run: GenerationRun,
) -> PreparationReadProjection:
    """Project historical exact-six preparation bindings for a SUCCEEDED run.

    Validates immutable generation identity and provenance structure. Does **not**
    require current_version_id equality or IN_REVIEW stewardship.
    """
    bindings = content_uow.versions.find_all_by_generation_run_id(
        run.generation_run_id.value
    )
    if not bindings:
        return PreparationReadProjection(status=PreparationReadProjectionStatus.ZERO)

    if len(bindings) != 6:
        return PreparationReadProjection(
            status=PreparationReadProjectionStatus.INVALID,
            detail=f"expected 6 bindings, found {len(bindings)}",
        )

    by_kind: dict[str, object] = {}
    projected: list[ProjectedPreparationArtifact] = []
    correlation_ids: set[UUID] = set()
    provider_ids: set[str] = set()
    model_ids: set[str] = set()
    source_ref_tuples: set[tuple[tuple[str, str, int | None], ...]] = set()
    generation_run_refs: list[ResourceRef] = []
    work_refs: list[ResourceRef] = []

    for binding in bindings:
        provenance = binding.provenance
        if not isinstance(provenance, AIGenerationProvenanceV2):
            return PreparationReadProjection(
                status=PreparationReadProjectionStatus.INVALID,
                detail="binding provenance is not AIGenerationProvenanceV2",
            )
        kind = binding.artifact_kind
        if kind is None or kind not in PREPARATION_ARTIFACT_KINDS:
            return PreparationReadProjection(
                status=PreparationReadProjectionStatus.INVALID,
                detail="binding has missing or unknown artifact_kind",
            )
        if kind in by_kind:
            return PreparationReadProjection(
                status=PreparationReadProjectionStatus.INVALID,
                detail=f"duplicate artifact_kind {kind}",
            )
        by_kind[kind] = binding

        run_ref = provenance.generation_run_ref
        if run_ref.resource_type != GENERATION_RUN_RESOURCE_TYPE:
            return PreparationReadProjection(
                status=PreparationReadProjectionStatus.INVALID,
                detail="generation_run_ref.resource_type mismatch",
            )
        if run_ref.resource_id != run.generation_run_id.value:
            return PreparationReadProjection(
                status=PreparationReadProjectionStatus.INVALID,
                detail="generation_run_ref.resource_id mismatch",
            )
        if run_ref.resource_revision is None:
            return PreparationReadProjection(
                status=PreparationReadProjectionStatus.INVALID,
                detail="generation_run_ref.resource_revision must be non-null",
            )
        if run_ref.resource_revision > run.aggregate_revision:
            return PreparationReadProjection(
                status=PreparationReadProjectionStatus.INVALID,
                detail="generation_run_ref.resource_revision exceeds run revision",
            )
        generation_run_refs.append(run_ref)

        if provenance.capability_id != CAPABILITY_EDUCATION_GENERATE_PREPARATION_KIT:
            return PreparationReadProjection(
                status=PreparationReadProjectionStatus.INVALID,
                detail="capability_id mismatch",
            )
        if provenance.provider_id != run.provider_id:
            return PreparationReadProjection(
                status=PreparationReadProjectionStatus.INVALID,
                detail="provenance provider_id disagrees with GenerationRun",
            )
        if provenance.model_id != run.model_id:
            return PreparationReadProjection(
                status=PreparationReadProjectionStatus.INVALID,
                detail="provenance model_id disagrees with GenerationRun",
            )

        if binding.version.tenant_id != run.tenant_id:
            return PreparationReadProjection(
                status=PreparationReadProjectionStatus.INVALID,
                detail="ContentVersion tenant mismatch",
            )

        correlation_ids.add(provenance.correlation_id)
        provider_ids.add(provenance.provider_id)
        model_ids.add(provenance.model_id)
        source_ref_tuples.add(
            tuple(
                (ref.resource_type, str(ref.resource_id), ref.resource_revision)
                for ref in provenance.source_refs
            )
        )

        work_matches = [
            ref
            for ref in provenance.source_refs
            if ref.resource_type == WORK_RESOURCE_TYPE
            and ref.resource_id == run.work_resource_id
            and ref.resource_revision == run.work_resource_revision
        ]
        if len(work_matches) != 1:
            return PreparationReadProjection(
                status=PreparationReadProjectionStatus.INVALID,
                detail="exact teaching.work revision missing from source_refs",
            )
        work_refs.append(work_matches[0])

        content = content_uow.contents.get(binding.version.content_id)
        if content is None:
            return PreparationReadProjection(
                status=PreparationReadProjectionStatus.INVALID,
                detail="Content aggregate missing for binding",
            )
        if content.tenant_id != run.tenant_id:
            return PreparationReadProjection(
                status=PreparationReadProjectionStatus.INVALID,
                detail="Content tenant mismatch",
            )
        expected_type = _KIND_TO_CONTENT_TYPE[kind]
        if str(content.content_type) != expected_type:
            return PreparationReadProjection(
                status=PreparationReadProjectionStatus.INVALID,
                detail=f"Content type mismatch for {kind}",
            )

        projected.append(
            ProjectedPreparationArtifact(
                artifact_kind=kind,
                content_id=content.content_id.value,
                version_id=binding.version.version_id.value,
                content_type=expected_type,
                title=content.title,
                stewardship_state=content.stewardship_state.value,
                aggregate_revision=int(content.aggregate_revision),
            )
        )

    if set(by_kind) != set(PREPARATION_ARTIFACT_KINDS):
        return PreparationReadProjection(
            status=PreparationReadProjectionStatus.INVALID,
            detail="artifact_kind set is incomplete",
        )
    if len(correlation_ids) != 1 or len(provider_ids) != 1 or len(model_ids) != 1:
        return PreparationReadProjection(
            status=PreparationReadProjectionStatus.INVALID,
            detail="provider/model/correlation not uniform across bindings",
        )
    if len(source_ref_tuples) != 1:
        return PreparationReadProjection(
            status=PreparationReadProjectionStatus.INVALID,
            detail="source_refs not uniform across bindings",
        )

    first_run_ref = generation_run_refs[0]
    for run_ref in generation_run_refs[1:]:
        if (
            run_ref.resource_type != first_run_ref.resource_type
            or run_ref.resource_id != first_run_ref.resource_id
            or run_ref.resource_revision != first_run_ref.resource_revision
        ):
            return PreparationReadProjection(
                status=PreparationReadProjectionStatus.INVALID,
                detail="generation_run_ref not uniform across bindings",
            )

    ordered = tuple(
        next(a for a in projected if a.artifact_kind == kind)
        for kind in PREPARATION_ARTIFACT_KINDS
    )
    return PreparationReadProjection(
        status=PreparationReadProjectionStatus.EXACT_SIX,
        projection=ExactSixPreparationProjection(
            artifacts=ordered,
            correlation_id=next(iter(correlation_ids)),
            provider_id=next(iter(provider_ids)),
            model_id=next(iter(model_ids)),
            work_ref=work_refs[0],
            generation_run_ref=first_run_ref,
        ),
    )

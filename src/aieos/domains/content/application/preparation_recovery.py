"""Read-only exact-six preparation Content recovery (TOS-DEV04-I06/I06R1).

Validates committed AI GenerationRun bindings against ADR-AIEOS-052 invariants.
Does not mutate GenerationRun or Content.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from uuid import UUID

from aieos.domains.content.application.ports import ContentUnitOfWork
from aieos.domains.content.domain.provenance import AIGenerationProvenanceV2
from aieos.domains.content.domain.states import StewardshipState
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


class PreparationBindingRecoveryStatus(Enum):
    ZERO = "zero"
    EXACT_SIX = "exact_six"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class RecoveredPreparationArtifact:
    artifact_kind: str
    content_id: UUID
    version_id: UUID
    content_type: str
    title: str
    stewardship_state: str
    aggregate_revision: int


@dataclass(frozen=True, slots=True)
class ExactSixPreparationRecovery:
    artifacts: tuple[RecoveredPreparationArtifact, ...]
    correlation_id: UUID
    provider_id: str
    model_id: str
    work_ref: ResourceRef
    generation_run_ref: ResourceRef


@dataclass(frozen=True, slots=True)
class PreparationBindingInspection:
    status: PreparationBindingRecoveryStatus
    recovery: ExactSixPreparationRecovery | None = None
    detail: str | None = None


def inspect_preparation_generation_bindings(
    content_uow: ContentUnitOfWork,
    run: GenerationRun,
) -> PreparationBindingInspection:
    """Inspect Content bindings for a preparation GenerationRun.

    Returns ZERO, EXACT_SIX, or INVALID. Does not raise for INVALID — caller
    decides fail-closed policy.
    """
    bindings = content_uow.versions.find_all_by_generation_run_id(
        run.generation_run_id.value
    )
    if not bindings:
        return PreparationBindingInspection(status=PreparationBindingRecoveryStatus.ZERO)

    if len(bindings) != 6:
        return PreparationBindingInspection(
            status=PreparationBindingRecoveryStatus.INVALID,
            detail=f"expected 6 bindings, found {len(bindings)}",
        )

    by_kind: dict[str, object] = {}
    recovered: list[RecoveredPreparationArtifact] = []
    correlation_ids: set[UUID] = set()
    provider_ids: set[str] = set()
    model_ids: set[str] = set()
    source_ref_tuples: set[tuple[tuple[str, str, int | None], ...]] = set()
    generation_run_refs: list[ResourceRef] = []
    work_refs: list[ResourceRef] = []

    for binding in bindings:
        provenance = binding.provenance
        if not isinstance(provenance, AIGenerationProvenanceV2):
            return PreparationBindingInspection(
                status=PreparationBindingRecoveryStatus.INVALID,
                detail="binding provenance is not AIGenerationProvenanceV2",
            )
        kind = binding.artifact_kind
        if kind is None or kind not in PREPARATION_ARTIFACT_KINDS:
            return PreparationBindingInspection(
                status=PreparationBindingRecoveryStatus.INVALID,
                detail="binding has missing or unknown artifact_kind",
            )
        if kind in by_kind:
            return PreparationBindingInspection(
                status=PreparationBindingRecoveryStatus.INVALID,
                detail=f"duplicate artifact_kind {kind}",
            )
        by_kind[kind] = binding

        run_ref = provenance.generation_run_ref
        if run_ref.resource_type != GENERATION_RUN_RESOURCE_TYPE:
            return PreparationBindingInspection(
                status=PreparationBindingRecoveryStatus.INVALID,
                detail="generation_run_ref.resource_type mismatch",
            )
        if run_ref.resource_id != run.generation_run_id.value:
            return PreparationBindingInspection(
                status=PreparationBindingRecoveryStatus.INVALID,
                detail="generation_run_ref.resource_id mismatch",
            )
        if run_ref.resource_revision is None:
            return PreparationBindingInspection(
                status=PreparationBindingRecoveryStatus.INVALID,
                detail="generation_run_ref.resource_revision must be non-null",
            )
        if run_ref.resource_revision > run.aggregate_revision:
            return PreparationBindingInspection(
                status=PreparationBindingRecoveryStatus.INVALID,
                detail="generation_run_ref.resource_revision exceeds run revision",
            )
        generation_run_refs.append(run_ref)

        if provenance.capability_id != CAPABILITY_EDUCATION_GENERATE_PREPARATION_KIT:
            return PreparationBindingInspection(
                status=PreparationBindingRecoveryStatus.INVALID,
                detail="capability_id mismatch",
            )
        if provenance.provider_id != run.provider_id:
            return PreparationBindingInspection(
                status=PreparationBindingRecoveryStatus.INVALID,
                detail="provenance provider_id disagrees with GenerationRun",
            )
        if provenance.model_id != run.model_id:
            return PreparationBindingInspection(
                status=PreparationBindingRecoveryStatus.INVALID,
                detail="provenance model_id disagrees with GenerationRun",
            )

        if binding.version.tenant_id != run.tenant_id:
            return PreparationBindingInspection(
                status=PreparationBindingRecoveryStatus.INVALID,
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
            return PreparationBindingInspection(
                status=PreparationBindingRecoveryStatus.INVALID,
                detail="exact teaching.work revision missing from source_refs",
            )
        work_refs.append(work_matches[0])

        content = content_uow.contents.get(binding.version.content_id)
        if content is None:
            return PreparationBindingInspection(
                status=PreparationBindingRecoveryStatus.INVALID,
                detail="Content aggregate missing for binding",
            )
        if content.tenant_id != run.tenant_id:
            return PreparationBindingInspection(
                status=PreparationBindingRecoveryStatus.INVALID,
                detail="Content tenant mismatch",
            )
        if content.current_version_id != binding.version.version_id:
            return PreparationBindingInspection(
                status=PreparationBindingRecoveryStatus.INVALID,
                detail="Content current_version_id mismatch",
            )
        if content.stewardship_state is not StewardshipState.IN_REVIEW:
            return PreparationBindingInspection(
                status=PreparationBindingRecoveryStatus.INVALID,
                detail="Content stewardship_state is not IN_REVIEW",
            )
        expected_type = _KIND_TO_CONTENT_TYPE[kind]
        if str(content.content_type) != expected_type:
            return PreparationBindingInspection(
                status=PreparationBindingRecoveryStatus.INVALID,
                detail=f"Content type mismatch for {kind}",
            )

        recovered.append(
            RecoveredPreparationArtifact(
                artifact_kind=kind,
                content_id=content.content_id.value,
                version_id=binding.version.version_id.value,
                content_type=expected_type,
                title=content.title,
                stewardship_state=StewardshipState.IN_REVIEW.value,
                aggregate_revision=int(content.aggregate_revision),
            )
        )

    if set(by_kind) != set(PREPARATION_ARTIFACT_KINDS):
        return PreparationBindingInspection(
            status=PreparationBindingRecoveryStatus.INVALID,
            detail="artifact_kind set is incomplete",
        )
    if len(correlation_ids) != 1 or len(provider_ids) != 1 or len(model_ids) != 1:
        return PreparationBindingInspection(
            status=PreparationBindingRecoveryStatus.INVALID,
            detail="provider/model/correlation not uniform across bindings",
        )
    if len(source_ref_tuples) != 1:
        return PreparationBindingInspection(
            status=PreparationBindingRecoveryStatus.INVALID,
            detail="source_refs not uniform across bindings",
        )

    first_run_ref = generation_run_refs[0]
    for run_ref in generation_run_refs[1:]:
        if (
            run_ref.resource_type != first_run_ref.resource_type
            or run_ref.resource_id != first_run_ref.resource_id
            or run_ref.resource_revision != first_run_ref.resource_revision
        ):
            return PreparationBindingInspection(
                status=PreparationBindingRecoveryStatus.INVALID,
                detail="generation_run_ref not uniform across bindings",
            )

    ordered = tuple(
        next(a for a in recovered if a.artifact_kind == kind)
        for kind in PREPARATION_ARTIFACT_KINDS
    )
    return PreparationBindingInspection(
        status=PreparationBindingRecoveryStatus.EXACT_SIX,
        recovery=ExactSixPreparationRecovery(
            artifacts=ordered,
            correlation_id=next(iter(correlation_ids)),
            provider_id=next(iter(provider_ids)),
            model_id=next(iter(model_ids)),
            work_ref=work_refs[0],
            generation_run_ref=first_run_ref,
        ),
    )

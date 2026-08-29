"""Typed ContentVersion + AI provenance bindings for generation-run lookups."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from aieos.domains.content.domain.provenance import AIGenerationProvenance
from aieos.domains.content.domain.version import ContentVersion


@dataclass(frozen=True, slots=True)
class ContentVersionAIGenerationBinding:
    """Immutable read model: ContentVersion plus parsed AI provenance (V1|V2)."""

    version: ContentVersion
    provenance: AIGenerationProvenance

    @property
    def generation_run_id(self) -> UUID:
        return self.provenance.generation_run_ref.resource_id

    @property
    def artifact_kind(self) -> str | None:
        artifact_kind = getattr(self.provenance, "artifact_kind", None)
        return artifact_kind if isinstance(artifact_kind, str) else None

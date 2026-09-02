"""TeachingExecutionContentBinding — exact ContentVersion identity used in execution.

Immutable after TeachingExecution start. Does not copy Content payload, does not
follow current/published pointers, and does not introduce PreparationKit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final
from uuid import UUID

from aieos.domains.teaching.domain.errors import InvalidTeachingExecutionError
from aieos.domains.teaching.domain.identities import ExecutionId, require_foreign_uuid

MAX_ARTIFACT_KIND_LENGTH: Final = 128


def _require_artifact_kind(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidTeachingExecutionError("artifact_kind must be a non-empty string")
    stripped = value.strip()
    if len(stripped) > MAX_ARTIFACT_KIND_LENGTH:
        raise InvalidTeachingExecutionError(
            f"artifact_kind must be at most {MAX_ARTIFACT_KIND_LENGTH} characters"
        )
    return stripped


@dataclass(frozen=True, slots=True)
class ContentBindingSpec:
    """Input for an exact ContentVersion binding at TeachingExecution start."""

    content_id: UUID
    content_version_id: UUID
    artifact_kind: str

    def __post_init__(self) -> None:
        require_foreign_uuid(self.content_id, label="content_id")
        require_foreign_uuid(self.content_version_id, label="content_version_id")
        object.__setattr__(
            self, "artifact_kind", _require_artifact_kind(self.artifact_kind)
        )


@dataclass(frozen=True, slots=True)
class TeachingExecutionContentBinding:
    """Exact immutable ContentVersion binding for a TeachingExecution."""

    execution_id: ExecutionId
    content_id: UUID
    content_version_id: UUID
    artifact_kind: str

    def __post_init__(self) -> None:
        if not isinstance(self.execution_id, ExecutionId):
            raise InvalidTeachingExecutionError(
                "execution_id must be an ExecutionId"
            )
        require_foreign_uuid(self.content_id, label="content_id")
        require_foreign_uuid(self.content_version_id, label="content_version_id")
        object.__setattr__(
            self, "artifact_kind", _require_artifact_kind(self.artifact_kind)
        )

    @classmethod
    def from_spec(
        cls,
        spec: ContentBindingSpec,
        *,
        execution_id: ExecutionId,
    ) -> TeachingExecutionContentBinding:
        return cls(
            execution_id=execution_id,
            content_id=spec.content_id,
            content_version_id=spec.content_version_id,
            artifact_kind=spec.artifact_kind,
        )

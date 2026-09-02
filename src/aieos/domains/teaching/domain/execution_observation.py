"""TeachingExecutionObservation — private note or class-level observation.

Learner-specific observations, scores, attendance, mastery, and diagnosis are
forbidden. Mutable only while the parent TeachingExecution is IN_PROGRESS.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from aieos.domains.teaching.domain.errors import (
    InvalidTeachingExecutionObservationError,
)
from aieos.domains.teaching.domain.identities import (
    ExecutionId,
    ObservationId,
    ObservationRevision,
)
from aieos.domains.teaching.domain.observation_kind import (
    ObservationKind,
    parse_observation_kind,
)

MAX_OBSERVATION_BODY_LENGTH: Final = 16_384


def _require_aware(value: datetime, *, label: str) -> datetime:
    if not isinstance(value, datetime):
        raise InvalidTeachingExecutionObservationError(f"{label} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise InvalidTeachingExecutionObservationError(
            f"{label} must be timezone-aware"
        )
    return value


def _require_body(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidTeachingExecutionObservationError(
            "body must be a non-empty string"
        )
    stripped = value.strip()
    if len(stripped) > MAX_OBSERVATION_BODY_LENGTH:
        raise InvalidTeachingExecutionObservationError(
            f"body must be at most {MAX_OBSERVATION_BODY_LENGTH} characters"
        )
    return stripped


@dataclass(frozen=True, slots=True)
class TeachingExecutionObservation:
    """Teacher-authored observation captured during classroom execution."""

    observation_id: ObservationId
    execution_id: ExecutionId
    observation_kind: ObservationKind
    body: str
    recorded_at: datetime
    updated_at: datetime
    revision: ObservationRevision

    def __post_init__(self) -> None:
        set_ = object.__setattr__
        if not isinstance(self.observation_id, ObservationId):
            raise InvalidTeachingExecutionObservationError(
                "observation_id must be an ObservationId"
            )
        if not isinstance(self.execution_id, ExecutionId):
            raise InvalidTeachingExecutionObservationError(
                "execution_id must be an ExecutionId"
            )
        set_(self, "observation_kind", parse_observation_kind(self.observation_kind))
        set_(self, "body", _require_body(self.body))
        if not isinstance(self.revision, ObservationRevision):
            raise InvalidTeachingExecutionObservationError(
                "revision must be an ObservationRevision"
            )
        _require_aware(self.recorded_at, label="recorded_at")
        _require_aware(self.updated_at, label="updated_at")

    @classmethod
    def create(
        cls,
        *,
        execution_id: ExecutionId,
        observation_kind: ObservationKind | str,
        body: str,
        recorded_at: datetime,
        observation_id: ObservationId | None = None,
    ) -> TeachingExecutionObservation:
        _require_aware(recorded_at, label="recorded_at")
        return cls(
            observation_id=(
                ObservationId.generate()
                if observation_id is None
                else observation_id
            ),
            execution_id=execution_id,
            observation_kind=observation_kind,
            body=body,
            recorded_at=recorded_at,
            updated_at=recorded_at,
            revision=ObservationRevision(0),
        )

    def correct(
        self,
        *,
        body: str,
        updated_at: datetime,
    ) -> TeachingExecutionObservation:
        """Correct observation text; bumps observation revision."""
        _require_aware(updated_at, label="updated_at")
        return dataclasses.replace(
            self,
            body=_require_body(body),
            revision=self.revision.next(),
            updated_at=updated_at,
        )

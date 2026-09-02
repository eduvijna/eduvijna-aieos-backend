"""TeachingExecution aggregate contract.

TeachingExecution is the Teaching-domain System of Record for actual classroom
teaching. COMPLETED means only that the represented human teacher recorded the
execution as completed — not assignment closed, delivery, attempt, submission,
assessment, grade, or mastery.

teacher_principal_id is the represented / effective HUMAN teacher whose
classroom execution is being recorded — not the HTTP caller, service workload,
or machine identity by default.

class_ref is an opaque School Context identifier. Current-authority ClassRef
validation belongs to DEV07-I02 — this factory performs intrinsic validation
only.

No PreparationKit aggregate. Content bindings reference exact ContentVersion
identities only.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final
from uuid import UUID

from aieos.domains.teaching.domain.errors import (
    InvalidTeachingExecutionError,
    InvalidTeachingExecutionObservationError,
)
from aieos.domains.teaching.domain.execution_content_binding import (
    ContentBindingSpec,
    TeachingExecutionContentBinding,
)
from aieos.domains.teaching.domain.execution_lifecycle import (
    ExecutionLifecycleState,
    parse_execution_lifecycle_state,
)
from aieos.domains.teaching.domain.execution_observation import (
    TeachingExecutionObservation,
)
from aieos.domains.teaching.domain.identities import (
    AggregateRevision,
    ExecutionId,
    WorkId,
    require_foreign_uuid,
)
from aieos.domains.teaching.domain.observation_kind import ObservationKind

MAX_CLASS_REF_LENGTH: Final = 512


def _require_aware(value: datetime, *, label: str) -> datetime:
    if not isinstance(value, datetime):
        raise InvalidTeachingExecutionError(f"{label} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise InvalidTeachingExecutionError(f"{label} must be timezone-aware")
    return value


def _require_text(value: str, *, label: str, max_length: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidTeachingExecutionError(f"{label} must be a non-empty string")
    stripped = value.strip()
    if len(stripped) > max_length:
        raise InvalidTeachingExecutionError(
            f"{label} must be at most {max_length} characters"
        )
    return stripped


@dataclass(frozen=True, slots=True)
class TeachingExecution:
    """Durable teacher classroom execution snapshot."""

    execution_id: ExecutionId
    tenant_id: UUID
    teacher_principal_id: UUID
    work_id: WorkId
    class_ref: str
    lifecycle_state: ExecutionLifecycleState
    started_at: datetime
    completed_at: datetime | None
    cancelled_at: datetime | None
    aggregate_revision: AggregateRevision
    created_at: datetime
    updated_at: datetime
    bindings: tuple[TeachingExecutionContentBinding, ...] = ()

    def __post_init__(self) -> None:
        set_ = object.__setattr__
        set_(
            self,
            "lifecycle_state",
            parse_execution_lifecycle_state(self.lifecycle_state),
        )
        set_(
            self,
            "class_ref",
            _require_text(
                self.class_ref, label="class_ref", max_length=MAX_CLASS_REF_LENGTH
            ),
        )
        require_foreign_uuid(self.tenant_id, label="tenant_id")
        require_foreign_uuid(
            self.teacher_principal_id, label="teacher_principal_id"
        )
        if not isinstance(self.execution_id, ExecutionId):
            raise InvalidTeachingExecutionError(
                "execution_id must be an ExecutionId"
            )
        if not isinstance(self.work_id, WorkId):
            raise InvalidTeachingExecutionError("work_id must be a WorkId")
        if not isinstance(self.aggregate_revision, AggregateRevision):
            raise InvalidTeachingExecutionError(
                "aggregate_revision must be an AggregateRevision"
            )
        if not isinstance(self.bindings, tuple):
            set_(self, "bindings", tuple(self.bindings))
        for binding in self.bindings:
            if not isinstance(binding, TeachingExecutionContentBinding):
                raise InvalidTeachingExecutionError(
                    "bindings must contain TeachingExecutionContentBinding values"
                )
            if binding.execution_id != self.execution_id:
                raise InvalidTeachingExecutionError(
                    "binding.execution_id must match the parent execution_id"
                )
        _require_aware(self.started_at, label="started_at")
        _require_aware(self.created_at, label="created_at")
        _require_aware(self.updated_at, label="updated_at")
        if self.completed_at is not None:
            _require_aware(self.completed_at, label="completed_at")
        if self.cancelled_at is not None:
            _require_aware(self.cancelled_at, label="cancelled_at")
        self._assert_lifecycle_timestamps()

    def _assert_lifecycle_timestamps(self) -> None:
        state = self.lifecycle_state
        if state is ExecutionLifecycleState.IN_PROGRESS:
            if self.completed_at is not None or self.cancelled_at is not None:
                raise InvalidTeachingExecutionError(
                    "IN_PROGRESS execution must have completed_at and "
                    "cancelled_at unset"
                )
        elif state is ExecutionLifecycleState.COMPLETED:
            if self.completed_at is None or self.cancelled_at is not None:
                raise InvalidTeachingExecutionError(
                    "COMPLETED execution requires completed_at and unset "
                    "cancelled_at"
                )
        elif state is ExecutionLifecycleState.CANCELLED:
            if self.cancelled_at is None or self.completed_at is not None:
                raise InvalidTeachingExecutionError(
                    "CANCELLED execution requires cancelled_at and unset "
                    "completed_at"
                )

    @property
    def is_terminal(self) -> bool:
        return self.lifecycle_state in (
            ExecutionLifecycleState.COMPLETED,
            ExecutionLifecycleState.CANCELLED,
        )

    def assert_observations_mutable(self) -> None:
        if self.is_terminal:
            raise InvalidTeachingExecutionObservationError(
                "observations are immutable after the parent TeachingExecution "
                "becomes COMPLETED or CANCELLED"
            )

    @classmethod
    def start(
        cls,
        *,
        tenant_id: UUID,
        teacher_principal_id: UUID,
        work_id: WorkId,
        class_ref: str,
        started_at: datetime,
        bindings: Sequence[ContentBindingSpec | TeachingExecutionContentBinding]
        | None = None,
        execution_id: ExecutionId | None = None,
    ) -> TeachingExecution:
        """Materialize a new IN_PROGRESS TeachingExecution.

        Intrinsic validation only. Does not prove ClassRef current authority
        (DEV07-I02). Bindings are immutable after this start.
        """
        _require_aware(started_at, label="started_at")
        eid = ExecutionId.generate() if execution_id is None else execution_id
        normalized_bindings: tuple[TeachingExecutionContentBinding, ...]
        if bindings is None:
            normalized_bindings = ()
        else:
            rebuilt: list[TeachingExecutionContentBinding] = []
            seen: set[tuple[UUID, UUID]] = set()
            for item in bindings:
                if isinstance(item, ContentBindingSpec):
                    binding = TeachingExecutionContentBinding.from_spec(
                        item, execution_id=eid
                    )
                elif isinstance(item, TeachingExecutionContentBinding):
                    if item.execution_id != eid:
                        binding = TeachingExecutionContentBinding(
                            execution_id=eid,
                            content_id=item.content_id,
                            content_version_id=item.content_version_id,
                            artifact_kind=item.artifact_kind,
                        )
                    else:
                        binding = item
                else:
                    raise InvalidTeachingExecutionError(
                        "bindings must be ContentBindingSpec or "
                        "TeachingExecutionContentBinding values"
                    )
                key = (binding.content_id, binding.content_version_id)
                if key in seen:
                    raise InvalidTeachingExecutionError(
                        "duplicate exact ContentVersion binding is not permitted"
                    )
                seen.add(key)
                rebuilt.append(binding)
            normalized_bindings = tuple(rebuilt)
        return cls(
            execution_id=eid,
            tenant_id=tenant_id,
            teacher_principal_id=teacher_principal_id,
            work_id=work_id,
            class_ref=class_ref,
            lifecycle_state=ExecutionLifecycleState.IN_PROGRESS,
            started_at=started_at,
            completed_at=None,
            cancelled_at=None,
            aggregate_revision=AggregateRevision(0),
            created_at=started_at,
            updated_at=started_at,
            bindings=normalized_bindings,
        )

    def complete(self, *, completed_at: datetime) -> TeachingExecution:
        if self.lifecycle_state is not ExecutionLifecycleState.IN_PROGRESS:
            raise InvalidTeachingExecutionError(
                "only an IN_PROGRESS execution can be completed"
            )
        _require_aware(completed_at, label="completed_at")
        return dataclasses.replace(
            self,
            lifecycle_state=ExecutionLifecycleState.COMPLETED,
            completed_at=completed_at,
            cancelled_at=None,
            aggregate_revision=self.aggregate_revision.next(),
            updated_at=completed_at,
        )

    def cancel(self, *, cancelled_at: datetime) -> TeachingExecution:
        if self.lifecycle_state is not ExecutionLifecycleState.IN_PROGRESS:
            raise InvalidTeachingExecutionError(
                "only an IN_PROGRESS execution can be cancelled"
            )
        _require_aware(cancelled_at, label="cancelled_at")
        return dataclasses.replace(
            self,
            lifecycle_state=ExecutionLifecycleState.CANCELLED,
            cancelled_at=cancelled_at,
            completed_at=None,
            aggregate_revision=self.aggregate_revision.next(),
            updated_at=cancelled_at,
        )

    def create_observation(
        self,
        *,
        observation_kind: ObservationKind | str,
        body: str,
        recorded_at: datetime,
    ) -> TeachingExecutionObservation:
        self.assert_observations_mutable()
        return TeachingExecutionObservation.create(
            execution_id=self.execution_id,
            observation_kind=observation_kind,
            body=body,
            recorded_at=recorded_at,
        )

    def correct_observation(
        self,
        observation: TeachingExecutionObservation,
        *,
        body: str,
        updated_at: datetime,
    ) -> TeachingExecutionObservation:
        self.assert_observations_mutable()
        if observation.execution_id != self.execution_id:
            raise InvalidTeachingExecutionObservationError(
                "observation.execution_id must match the parent execution_id"
            )
        return observation.correct(body=body, updated_at=updated_at)

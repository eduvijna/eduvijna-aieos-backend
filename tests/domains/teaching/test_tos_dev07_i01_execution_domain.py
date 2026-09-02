"""TOS-DEV07-I01 — TeachingExecution domain aggregate tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from aieos.domains.teaching.domain.errors import (
    InvalidTeachingExecutionError,
    InvalidTeachingExecutionObservationError,
    InvalidTeachingIdentityError,
)
from aieos.domains.teaching.domain.execution import TeachingExecution
from aieos.domains.teaching.domain.execution_content_binding import ContentBindingSpec
from aieos.domains.teaching.domain.execution_lifecycle import ExecutionLifecycleState
from aieos.domains.teaching.domain.identities import (
    AggregateRevision,
    ExecutionId,
    ObservationId,
    WorkId,
)
from aieos.domains.teaching.domain.observation_kind import ObservationKind

pytestmark = pytest.mark.tos_dev07_i01

NOW = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)


def _start(**overrides) -> TeachingExecution:
    values = {
        "tenant_id": uuid4(),
        "teacher_principal_id": uuid4(),
        "work_id": WorkId.generate(),
        "class_ref": "class-5a",
        "started_at": NOW,
    }
    values.update(overrides)
    return TeachingExecution.start(**values)


class TestExecutionAndObservationIds:
    def test_generated_execution_id_is_uuidv7(self) -> None:
        execution_id = ExecutionId.generate()
        assert execution_id.value.version == 7
        assert str(execution_id) == str(execution_id.value)

    def test_generated_observation_id_is_uuidv7(self) -> None:
        observation_id = ObservationId.generate()
        assert observation_id.value.version == 7

    def test_non_v7_execution_id_rejected(self) -> None:
        with pytest.raises(InvalidTeachingIdentityError):
            ExecutionId(UUID("00000000-0000-4000-8000-000000000001"))

    def test_non_v7_observation_id_rejected(self) -> None:
        with pytest.raises(InvalidTeachingIdentityError):
            ObservationId(UUID("00000000-0000-4000-8000-000000000001"))


class TestTeachingExecutionStart:
    def test_start_defaults_in_progress_revision_zero(self) -> None:
        execution = _start()
        assert execution.lifecycle_state is ExecutionLifecycleState.IN_PROGRESS
        assert int(execution.aggregate_revision) == 0
        assert execution.completed_at is None
        assert execution.cancelled_at is None
        assert execution.started_at == NOW
        assert execution.bindings == ()
        assert execution.execution_id.value.version == 7

    def test_assigned_is_not_a_teaching_execution_state(self) -> None:
        with pytest.raises(InvalidTeachingExecutionError):
            TeachingExecution(
                execution_id=ExecutionId.generate(),
                tenant_id=uuid4(),
                teacher_principal_id=uuid4(),
                work_id=WorkId.generate(),
                class_ref="class-5a",
                lifecycle_state="ASSIGNED",  # type: ignore[arg-type]
                started_at=NOW,
                completed_at=None,
                cancelled_at=None,
                aggregate_revision=AggregateRevision(0),
                created_at=NOW,
                updated_at=NOW,
            )

    def test_invalid_lifecycle_states_rejected(self) -> None:
        for state in (
            "PLANNED",
            "SCHEDULED",
            "DELIVERED",
            "ASSESSED",
            "GRADED",
            "MASTERED",
        ):
            with pytest.raises(InvalidTeachingExecutionError):
                TeachingExecution(
                    execution_id=ExecutionId.generate(),
                    tenant_id=uuid4(),
                    teacher_principal_id=uuid4(),
                    work_id=WorkId.generate(),
                    class_ref="class-5a",
                    lifecycle_state=state,  # type: ignore[arg-type]
                    started_at=NOW,
                    completed_at=None,
                    cancelled_at=None,
                    aggregate_revision=AggregateRevision(0),
                    created_at=NOW,
                    updated_at=NOW,
                )

    def test_blank_class_ref_rejected(self) -> None:
        with pytest.raises(InvalidTeachingExecutionError):
            _start(class_ref="   ")

    def test_naive_started_at_rejected(self) -> None:
        with pytest.raises(InvalidTeachingExecutionError):
            _start(started_at=datetime(2026, 9, 2, 10, 0))

    def test_no_preparation_kit_fields(self) -> None:
        execution = _start()
        assert not hasattr(execution, "kit_id")
        assert not hasattr(execution, "kit_revision")
        assert not hasattr(execution, "kit_status")
        assert "PreparationKit" not in type(execution).__name__


class TestContentBindings:
    def test_zero_bindings_accepted(self) -> None:
        assert _start(bindings=()).bindings == ()

    def test_fewer_and_more_than_six_bindings_accepted(self) -> None:
        few = [
            ContentBindingSpec(
                content_id=uuid4(),
                content_version_id=uuid4(),
                artifact_kind="lesson_plan",
            )
        ]
        many = [
            ContentBindingSpec(
                content_id=uuid4(),
                content_version_id=uuid4(),
                artifact_kind=f"kind-{i}",
            )
            for i in range(8)
        ]
        assert len(_start(bindings=few).bindings) == 1
        assert len(_start(bindings=many).bindings) == 8

    def test_bindings_immutable_exact_content_version(self) -> None:
        content_id = uuid4()
        version_id = uuid4()
        execution = _start(
            bindings=[
                ContentBindingSpec(
                    content_id=content_id,
                    content_version_id=version_id,
                    artifact_kind="worksheet",
                )
            ]
        )
        completed = execution.complete(completed_at=NOW + timedelta(seconds=1))
        assert completed.bindings[0].content_id == content_id
        assert completed.bindings[0].content_version_id == version_id
        assert completed.bindings[0].artifact_kind == "worksheet"
        assert not hasattr(completed, "replace_bindings")


class TestTeachingExecutionLifecycle:
    def test_valid_completion(self) -> None:
        execution = _start()
        completed = execution.complete(completed_at=NOW + timedelta(seconds=5))
        assert completed.lifecycle_state is ExecutionLifecycleState.COMPLETED
        assert completed.completed_at is not None
        assert completed.cancelled_at is None
        assert int(completed.aggregate_revision) == 1

    def test_valid_cancellation(self) -> None:
        execution = _start()
        cancelled = execution.cancel(cancelled_at=NOW + timedelta(seconds=5))
        assert cancelled.lifecycle_state is ExecutionLifecycleState.CANCELLED
        assert cancelled.cancelled_at is not None
        assert cancelled.completed_at is None
        assert int(cancelled.aggregate_revision) == 1

    def test_terminal_cannot_transition_again(self) -> None:
        completed = _start().complete(completed_at=NOW + timedelta(seconds=1))
        with pytest.raises(InvalidTeachingExecutionError):
            completed.cancel(cancelled_at=NOW + timedelta(seconds=2))
        with pytest.raises(InvalidTeachingExecutionError):
            completed.complete(completed_at=NOW + timedelta(seconds=2))
        cancelled = _start().cancel(cancelled_at=NOW + timedelta(seconds=1))
        with pytest.raises(InvalidTeachingExecutionError):
            cancelled.complete(completed_at=NOW + timedelta(seconds=2))
        with pytest.raises(InvalidTeachingExecutionError):
            cancelled.cancel(cancelled_at=NOW + timedelta(seconds=2))

    def test_stale_aggregate_revision_semantics(self) -> None:
        execution = _start()
        expected = execution.aggregate_revision
        mutated = execution.complete(completed_at=NOW + timedelta(seconds=1))
        assert int(mutated.aggregate_revision) == int(expected) + 1
        assert int(expected) == 0


class TestObservations:
    def test_permitted_kinds_only(self) -> None:
        execution = _start()
        note = execution.create_observation(
            observation_kind=ObservationKind.PRIVATE_EXECUTION_NOTE,
            body="Visual fraction example worked well.",
            recorded_at=NOW,
        )
        obs = execution.create_observation(
            observation_kind=ObservationKind.CLASS_OBSERVATION,
            body="Several learners confused numerator and denominator.",
            recorded_at=NOW,
        )
        assert note.observation_kind is ObservationKind.PRIVATE_EXECUTION_NOTE
        assert obs.observation_kind is ObservationKind.CLASS_OBSERVATION
        with pytest.raises(InvalidTeachingExecutionObservationError):
            execution.create_observation(
                observation_kind="LEARNER_OBSERVATION",
                body="student 12 struggled",
                recorded_at=NOW,
            )

    def test_learner_specific_observation_rejected(self) -> None:
        execution = _start()
        for kind in (
            "LEARNER_NOTE",
            "STUDENT_OBSERVATION",
            "ATTENDANCE",
            "SCORE",
            "GRADE",
            "MASTERY",
            "DIAGNOSIS",
            "SUBMISSION",
            "ATTEMPT",
        ):
            with pytest.raises(InvalidTeachingExecutionObservationError):
                execution.create_observation(
                    observation_kind=kind,
                    body="not allowed",
                    recorded_at=NOW,
                )

    def test_observation_correction_while_in_progress(self) -> None:
        execution = _start()
        note = execution.create_observation(
            observation_kind=ObservationKind.PRIVATE_EXECUTION_NOTE,
            body="first draft",
            recorded_at=NOW,
        )
        corrected = execution.correct_observation(
            note,
            body="corrected note",
            updated_at=NOW + timedelta(seconds=1),
        )
        assert corrected.body == "corrected note"
        assert int(corrected.revision) == 1
        assert corrected.observation_id == note.observation_id

    def test_observation_mutation_after_completed_rejected(self) -> None:
        execution = _start()
        note = execution.create_observation(
            observation_kind=ObservationKind.PRIVATE_EXECUTION_NOTE,
            body="during lesson",
            recorded_at=NOW,
        )
        completed = execution.complete(completed_at=NOW + timedelta(seconds=1))
        with pytest.raises(InvalidTeachingExecutionObservationError):
            completed.correct_observation(
                note, body="too late", updated_at=NOW + timedelta(seconds=2)
            )
        with pytest.raises(InvalidTeachingExecutionObservationError):
            completed.create_observation(
                observation_kind=ObservationKind.CLASS_OBSERVATION,
                body="too late",
                recorded_at=NOW + timedelta(seconds=2),
            )

    def test_observation_mutation_after_cancelled_rejected(self) -> None:
        execution = _start()
        note = execution.create_observation(
            observation_kind=ObservationKind.CLASS_OBSERVATION,
            body="during lesson",
            recorded_at=NOW,
        )
        cancelled = execution.cancel(cancelled_at=NOW + timedelta(seconds=1))
        with pytest.raises(InvalidTeachingExecutionObservationError):
            cancelled.correct_observation(
                note, body="too late", updated_at=NOW + timedelta(seconds=2)
            )

    def test_stale_observation_revision_semantics(self) -> None:
        execution = _start()
        note = execution.create_observation(
            observation_kind=ObservationKind.PRIVATE_EXECUTION_NOTE,
            body="v0",
            recorded_at=NOW,
        )
        expected = note.revision
        corrected = note.correct(body="v1", updated_at=NOW + timedelta(seconds=1))
        assert int(corrected.revision) == int(expected) + 1
        assert int(expected) == 0

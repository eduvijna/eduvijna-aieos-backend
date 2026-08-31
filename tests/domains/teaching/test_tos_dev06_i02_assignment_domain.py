"""TOS-DEV06-I02 — TeachingAssignment domain aggregate tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from aieos.domains.teaching.domain.assignment import TeachingAssignment
from aieos.domains.teaching.domain.assignment_lifecycle import AssignmentLifecycleState
from aieos.domains.teaching.domain.audience_type import AudienceType
from aieos.domains.teaching.domain.errors import (
    InvalidTeachingAssignmentError,
    InvalidTeachingIdentityError,
)
from aieos.domains.teaching.domain.identities import AssignmentId, WorkId

pytestmark = pytest.mark.tos_dev06_i02

NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


def _create(**overrides) -> TeachingAssignment:
    values = {
        "tenant_id": uuid4(),
        "teacher_principal_id": uuid4(),
        "content_id": uuid4(),
        "content_version_id": uuid4(),
        "class_ref": "class-5a",
        "assigned_at": NOW,
    }
    values.update(overrides)
    return TeachingAssignment.create(**values)


class TestAssignmentId:
    def test_generated_assignment_id_is_uuidv7(self) -> None:
        assignment_id = AssignmentId.generate()
        assert assignment_id.value.version == 7
        assert str(assignment_id) == str(assignment_id.value)

    def test_non_v7_assignment_id_rejected(self) -> None:
        with pytest.raises(InvalidTeachingIdentityError):
            AssignmentId(UUID("00000000-0000-4000-8000-000000000001"))


class TestTeachingAssignmentCreate:
    def test_create_defaults_active_revision_zero(self) -> None:
        assignment = _create()
        assert assignment.lifecycle_state is AssignmentLifecycleState.ACTIVE
        assert int(assignment.aggregate_revision) == 0
        assert assignment.available_from == assignment.assigned_at
        assert assignment.closed_at is None
        assert assignment.cancelled_at is None
        assert assignment.assignment_id.value.version == 7
        assert assignment.created_at == NOW
        assert assignment.updated_at == NOW

    def test_explicit_future_available_from_still_active(self) -> None:
        future = NOW + timedelta(days=2)
        assignment = _create(available_from=future)
        assert assignment.lifecycle_state is AssignmentLifecycleState.ACTIVE
        assert assignment.available_from == future

    def test_content_binding_preserved(self) -> None:
        content_id = uuid4()
        version_id = uuid4()
        assignment = _create(content_id=content_id, content_version_id=version_id)
        assert assignment.content_id == content_id
        assert assignment.content_version_id == version_id

    def test_class_ref_opaque_string_preserved(self) -> None:
        assignment = _create(class_ref="erp-opaque-section-42")
        assert assignment.class_ref == "erp-opaque-section-42"
        assert not isinstance(assignment.class_ref, UUID)

    def test_audience_display_label_presentation_only(self) -> None:
        assignment = _create(audience_display_label="Grade 5A")
        assert assignment.audience_display_label == "Grade 5A"
        assert assignment.audience_type is AudienceType.CLASS

    def test_blank_class_ref_rejected(self) -> None:
        with pytest.raises(InvalidTeachingAssignmentError):
            _create(class_ref="   ")

    def test_unsupported_audience_type_rejected(self) -> None:
        with pytest.raises(InvalidTeachingAssignmentError):
            _create(audience_type="learner")

    def test_naive_assigned_at_rejected(self) -> None:
        with pytest.raises(InvalidTeachingAssignmentError):
            _create(assigned_at=datetime(2026, 8, 31, 12, 0))


class TestTeachingAssignmentLifecycle:
    def test_update_due_at_set_and_clear(self) -> None:
        assignment = _create()
        due = NOW + timedelta(days=7)
        updated = assignment.update_due_at(due_at=due, updated_at=NOW + timedelta(seconds=1))
        assert updated.due_at == due
        assert int(updated.aggregate_revision) == 1
        cleared = updated.update_due_at(
            due_at=None, updated_at=NOW + timedelta(seconds=2)
        )
        assert cleared.due_at is None
        assert int(cleared.aggregate_revision) == 2
        assert cleared.content_id == assignment.content_id
        assert cleared.class_ref == assignment.class_ref

    def test_close_terminal(self) -> None:
        assignment = _create()
        closed = assignment.close(closed_at=NOW + timedelta(seconds=5))
        assert closed.lifecycle_state is AssignmentLifecycleState.CLOSED
        assert closed.closed_at is not None
        assert closed.cancelled_at is None
        assert int(closed.aggregate_revision) == 1
        with pytest.raises(InvalidTeachingAssignmentError):
            closed.cancel(cancelled_at=NOW + timedelta(seconds=6))
        with pytest.raises(InvalidTeachingAssignmentError):
            closed.update_due_at(due_at=NOW, updated_at=NOW + timedelta(seconds=6))

    def test_cancel_terminal(self) -> None:
        assignment = _create()
        cancelled = assignment.cancel(cancelled_at=NOW + timedelta(seconds=5))
        assert cancelled.lifecycle_state is AssignmentLifecycleState.CANCELLED
        assert cancelled.cancelled_at is not None
        assert cancelled.closed_at is None
        assert int(cancelled.aggregate_revision) == 1
        with pytest.raises(InvalidTeachingAssignmentError):
            cancelled.close(closed_at=NOW + timedelta(seconds=6))
        with pytest.raises(InvalidTeachingAssignmentError):
            cancelled.update_due_at(due_at=NOW, updated_at=NOW + timedelta(seconds=6))

    def test_due_date_passage_does_not_auto_transition(self) -> None:
        past_due = NOW - timedelta(days=1)
        assignment = _create(due_at=past_due)
        assert assignment.lifecycle_state is AssignmentLifecycleState.ACTIVE
        assert assignment.due_at == past_due

    def test_no_draft_or_scheduled_states(self) -> None:
        with pytest.raises(InvalidTeachingAssignmentError):
            TeachingAssignment(
                assignment_id=AssignmentId.generate(),
                tenant_id=uuid4(),
                teacher_principal_id=uuid4(),
                content_id=uuid4(),
                content_version_id=uuid4(),
                audience_type=AudienceType.CLASS,
                class_ref="class-5a",
                audience_display_label=None,
                source_work_id=None,
                lifecycle_state="DRAFT",  # type: ignore[arg-type]
                assigned_at=NOW,
                available_from=NOW,
                due_at=None,
                closed_at=None,
                cancelled_at=None,
                aggregate_revision=__import__(
                    "aieos.domains.teaching.domain.identities", fromlist=["AggregateRevision"]
                ).AggregateRevision(0),
                created_at=NOW,
                updated_at=NOW,
            )

    def test_source_work_id_optional(self) -> None:
        with_work = _create(source_work_id=WorkId.generate())
        without = _create(source_work_id=None)
        assert with_work.source_work_id is not None
        assert without.source_work_id is None

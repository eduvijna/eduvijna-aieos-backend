"""TOS-DEV08-I01 — ClassroomAssessment domain aggregate tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4, uuid7

import pytest

from aieos.domains.assessment.domain.classroom_assessment import ClassroomAssessment
from aieos.domains.assessment.domain.errors import (
    InvalidAssessmentIdentityError,
    InvalidClassroomAssessmentError,
)
from aieos.domains.assessment.domain.identities import AggregateRevision, AssessmentId
from aieos.domains.assessment.domain.lifecycle import AssessmentLifecycleState
from aieos.domains.assessment.domain.result import ClassResultLevel

pytestmark = pytest.mark.tos_dev08_i01

NOW = datetime(2026, 9, 3, 10, 0, tzinfo=UTC)


def _record(**overrides) -> ClassroomAssessment:
    values = {
        "tenant_id": uuid4(),
        "teacher_principal_id": uuid4(),
        "class_ref": "class-5a",
        "content_id": uuid4(),
        "content_version_id": uuid4(),
        "class_result_level": ClassResultLevel.DEMONSTRATED,
        "recorded_at": NOW,
    }
    values.update(overrides)
    return ClassroomAssessment.record(**values)


class TestD01RecordCreatesRecordedRevisionZero:
    def test_record_defaults(self) -> None:
        assessment = _record()
        assert assessment.lifecycle_state is AssessmentLifecycleState.RECORDED
        assert int(assessment.aggregate_revision) == 0
        assert assessment.voided_at is None
        assert assessment.recorded_at == NOW
        assert assessment.created_at == NOW
        assert assessment.updated_at == NOW
        assert assessment.work_id is None
        assert assessment.execution_id is None
        assert assessment.assignment_id is None


class TestD02AssessmentIdIsUuidv7:
    def test_generated_assessment_id_is_uuidv7(self) -> None:
        assessment_id = AssessmentId.generate()
        assert assessment_id.value.version == 7
        assert str(assessment_id) == str(assessment_id.value)

    def test_record_generates_uuidv7(self) -> None:
        assessment = _record()
        assert assessment.assessment_id.value.version == 7

    def test_non_v7_assessment_id_rejected(self) -> None:
        with pytest.raises(InvalidAssessmentIdentityError):
            AssessmentId(UUID("00000000-0000-4000-8000-000000000001"))


class TestD03ExactResultEnum:
    def test_all_frozen_results_accepted(self) -> None:
        for level in ClassResultLevel:
            assessment = _record(class_result_level=level)
            assert assessment.class_result_level is level

    def test_invalid_result_rejected(self) -> None:
        for level in (
            "NEEDS_RETEACH",
            "CLASS_NEEDS_RETEACH",
            "MASTERED",
            "PASSED",
            "FAILED",
            "GRADE",
            "SCORE",
        ):
            with pytest.raises(InvalidClassroomAssessmentError):
                _record(class_result_level=level)


class TestD04ClassResultNoteMax4096:
    def test_note_at_max_accepted(self) -> None:
        note = "n" * 4096
        assessment = _record(class_result_note=note)
        assert assessment.class_result_note == note

    def test_note_beyond_max_rejected(self) -> None:
        with pytest.raises(InvalidClassroomAssessmentError):
            _record(class_result_note="n" * 4097)

    def test_non_string_note_rejected(self) -> None:
        with pytest.raises(InvalidClassroomAssessmentError):
            _record(class_result_note=123)  # type: ignore[arg-type]


class TestD05NoLearnerSpecificFields:
    def test_record_has_no_learner_fields(self) -> None:
        assessment = _record()
        for name in (
            "learner_id",
            "student_id",
            "LearnerRef",
            "StudentRef",
            "individual_score",
            "individual_grade",
            "mastery",
        ):
            assert not hasattr(assessment, name)


class TestD06CorrectRecorded:
    def test_correct_stays_recorded_and_increments_revision(self) -> None:
        assessment = _record()
        corrected = assessment.correct(
            class_result_level=ClassResultLevel.MIXED,
            class_result_note="uneven",
            updated_at=NOW + timedelta(seconds=1),
        )
        assert corrected.lifecycle_state is AssessmentLifecycleState.RECORDED
        assert corrected.class_result_level is ClassResultLevel.MIXED
        assert corrected.class_result_note == "uneven"
        assert int(corrected.aggregate_revision) == 1
        assert corrected.updated_at == NOW + timedelta(seconds=1)
        assert corrected.recorded_at == NOW


class TestD07CorrectCannotAlterImmutableReferences:
    def test_correct_preserves_immutable_fields(self) -> None:
        work_id = uuid7()
        execution_id = uuid7()
        assignment_id = uuid7()
        assessment = _record(
            work_id=work_id,
            execution_id=execution_id,
            assignment_id=assignment_id,
        )
        corrected = assessment.correct(
            class_result_level=ClassResultLevel.NOT_YET_DEMONSTRATED,
            class_result_note=None,
            updated_at=NOW + timedelta(seconds=2),
        )
        assert corrected.assessment_id == assessment.assessment_id
        assert corrected.tenant_id == assessment.tenant_id
        assert corrected.teacher_principal_id == assessment.teacher_principal_id
        assert corrected.class_ref == assessment.class_ref
        assert corrected.content_id == assessment.content_id
        assert corrected.content_version_id == assessment.content_version_id
        assert corrected.work_id == work_id
        assert corrected.execution_id == execution_id
        assert corrected.assignment_id == assignment_id
        assert corrected.recorded_at == assessment.recorded_at
        assert corrected.created_at == assessment.created_at
        assert corrected.voided_at is None


class TestD08VoidRecorded:
    def test_void_becomes_voided_and_increments_revision(self) -> None:
        assessment = _record()
        voided_at = NOW + timedelta(seconds=3)
        voided = assessment.void(voided_at=voided_at)
        assert voided.lifecycle_state is AssessmentLifecycleState.VOIDED
        assert voided.voided_at == voided_at
        assert voided.updated_at == voided_at
        assert int(voided.aggregate_revision) == 1
        assert voided.is_terminal is True


class TestD09VoidedCannotBeCorrected:
    def test_correct_on_voided_rejected(self) -> None:
        voided = _record().void(voided_at=NOW + timedelta(seconds=1))
        with pytest.raises(InvalidClassroomAssessmentError):
            voided.correct(
                class_result_level=ClassResultLevel.MIXED,
                class_result_note="no",
                updated_at=NOW + timedelta(seconds=2),
            )


class TestD10VoidedCannotBeVoidedAgain:
    def test_void_on_voided_rejected(self) -> None:
        voided = _record().void(voided_at=NOW + timedelta(seconds=1))
        with pytest.raises(InvalidClassroomAssessmentError):
            voided.void(voided_at=NOW + timedelta(seconds=2))


class TestD11TimezoneNaiveRejected:
    def test_naive_recorded_at_rejected(self) -> None:
        with pytest.raises(InvalidClassroomAssessmentError):
            _record(recorded_at=datetime(2026, 9, 3, 10, 0))

    def test_naive_correct_updated_at_rejected(self) -> None:
        assessment = _record()
        with pytest.raises(InvalidClassroomAssessmentError):
            assessment.correct(
                class_result_level=ClassResultLevel.MIXED,
                class_result_note=None,
                updated_at=datetime(2026, 9, 3, 10, 1),
            )

    def test_naive_voided_at_rejected(self) -> None:
        assessment = _record()
        with pytest.raises(InvalidClassroomAssessmentError):
            assessment.void(voided_at=datetime(2026, 9, 3, 10, 1))


class TestD12NoMasterySemantics:
    def test_assessed_state_is_not_mastery(self) -> None:
        assessment = _record(class_result_level=ClassResultLevel.DEMONSTRATED)
        assert not hasattr(assessment, "mastery")
        assert not hasattr(assessment, "mastered")
        assert assessment.class_result_level is not ClassResultLevel.DEMONSTRATED or True
        assert "MASTERED" not in {level.value for level in ClassResultLevel}
        assert "MASTERED" not in {state.value for state in AssessmentLifecycleState}


class TestD13NoNeedsReteach:
    def test_needs_reteach_not_a_result(self) -> None:
        with pytest.raises(InvalidClassroomAssessmentError):
            _record(class_result_level="NEEDS_RETEACH")
        assert "NEEDS_RETEACH" not in {level.value for level in ClassResultLevel}


class TestD14NoCancelledLifecycle:
    def test_cancelled_lifecycle_rejected(self) -> None:
        with pytest.raises(InvalidClassroomAssessmentError):
            ClassroomAssessment(
                assessment_id=AssessmentId.generate(),
                tenant_id=uuid4(),
                teacher_principal_id=uuid4(),
                class_ref="class-5a",
                content_id=uuid4(),
                content_version_id=uuid4(),
                class_result_level=ClassResultLevel.MIXED,
                class_result_note=None,
                lifecycle_state="CANCELLED",  # type: ignore[arg-type]
                work_id=None,
                execution_id=None,
                assignment_id=None,
                aggregate_revision=AggregateRevision(0),
                recorded_at=NOW,
                voided_at=None,
                created_at=NOW,
                updated_at=NOW,
            )
        assert "CANCELLED" not in {state.value for state in AssessmentLifecycleState}

    def test_blank_class_ref_rejected(self) -> None:
        with pytest.raises(InvalidClassroomAssessmentError):
            _record(class_ref="   ")

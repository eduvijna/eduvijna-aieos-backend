"""TOS-DEV09-I01 — remediation TeachingWork + origin domain tests."""

from __future__ import annotations

from datetime import UTC, datetime, date
from uuid import uuid4, uuid7

import pytest

from aieos.domains.teaching.domain.class_result_level_snapshot import (
    ClassResultLevelSnapshot,
)
from aieos.domains.teaching.domain.errors import (
    InvalidIntentTypeError,
    InvalidRemediationOriginError,
)
from aieos.domains.teaching.domain.intent_type import IntentType, parse_intent_type
from aieos.domains.teaching.domain.remediation_origin import (
    TeachingWorkRemediationOrigin,
    create_remediation_teaching_work_with_origin,
)
from aieos.domains.teaching.domain.work import TeachingWork

pytestmark = pytest.mark.tos_dev09_i01

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
TARGET = date(2026, 9, 5)


def _pair(**overrides):
    values = {
        "tenant_id": uuid4(),
        "teacher_principal_id": uuid4(),
        "goal_text": "Reteach fractions with class-level practice",
        "target_date": TARGET,
        "locale": "en-IN",
        "created_at": NOW,
        "source_assessment_id": uuid4(),
        "source_assessment_aggregate_revision": 3,
        "source_class_result_level_snapshot": ClassResultLevelSnapshot.MIXED,
        "source_class_ref": "class-5a",
        "source_content_id": uuid4(),
        "source_content_version_id": uuid4(),
    }
    values.update(overrides)
    return create_remediation_teaching_work_with_origin(**values)


class TestIntentTypeWidening:
    def test_prepare_tomorrow_still_parses(self) -> None:
        assert parse_intent_type("prepare_tomorrow") is IntentType.PREPARE_TOMORROW

    def test_remediate_class_parses(self) -> None:
        assert parse_intent_type("remediate_class") is IntentType.REMEDIATE_CLASS

    def test_unknown_intent_rejected(self) -> None:
        with pytest.raises(InvalidIntentTypeError):
            parse_intent_type("improve")

    def test_prepare_tomorrow_create_still_works(self) -> None:
        work = TeachingWork.create_from_intent(
            tenant_id=uuid4(),
            teacher_principal_id=uuid4(),
            intent_type=IntentType.PREPARE_TOMORROW,
            goal_text="Prepare fractions",
            target_date=TARGET,
            locale="en-IN",
            created_at=NOW,
        )
        assert work.intent_type is IntentType.PREPARE_TOMORROW


class TestRemediationPairConstruction:
    def test_pair_is_coherent(self) -> None:
        work, origin = _pair()
        assert work.intent_type is IntentType.REMEDIATE_CLASS
        assert origin.work_id == work.work_id
        assert origin.tenant_id == work.tenant_id
        assert origin.initiating_teacher_principal_id == work.teacher_principal_id
        assert origin.created_at == work.created_at
        assert origin.source_assessment_aggregate_revision == 3
        assert (
            origin.source_class_result_level_snapshot is ClassResultLevelSnapshot.MIXED
        )

    def test_work_id_is_uuidv7(self) -> None:
        work, _origin = _pair()
        assert work.work_id.value.version == 7


class TestSnapshotVocabulary:
    def test_three_frozen_values_accepted(self) -> None:
        for level in ClassResultLevelSnapshot:
            _work, origin = _pair(source_class_result_level_snapshot=level)
            assert origin.source_class_result_level_snapshot is level

    def test_invalid_snapshot_rejected(self) -> None:
        for bad in ("MASTERED", "NEEDS_RETEACH", "PASSED", "improve"):
            with pytest.raises(InvalidRemediationOriginError):
                _pair(source_class_result_level_snapshot=bad)

    def test_negative_revision_rejected(self) -> None:
        with pytest.raises(InvalidRemediationOriginError):
            _pair(source_assessment_aggregate_revision=-1)

    def test_zero_revision_accepted(self) -> None:
        _work, origin = _pair(source_assessment_aggregate_revision=0)
        assert origin.source_assessment_aggregate_revision == 0


class TestOriginImmutabilityAndShape:
    def test_origin_is_frozen(self) -> None:
        _work, origin = _pair()
        with pytest.raises(Exception):
            origin.source_class_ref = "mutated"  # type: ignore[misc]

    def test_no_forbidden_fields(self) -> None:
        fields = set(TeachingWorkRemediationOrigin.__dataclass_fields__)
        forbidden = {
            "updated_at",
            "class_result_note",
            "metadata",
            "learner_id",
            "student_id",
            "mastery",
            "include_class_result_note_in_goal_context",
            "include_selected_observation_ids",
            "observation_body",
            "private_execution_note",
        }
        assert fields.isdisjoint(forbidden)
        assert "created_at" in fields

"""TOS-DEV04-I04 preparation artifact builder and Answer Key proofs."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from aieos.domains.education.application.preparation_artifacts import (
    PreparationArtifactBuildFailed,
    build_answer_key_v1,
    build_preparation_artifact_payloads,
)
from aieos.domains.education.preparation_kit_v1 import (
    AnswerKeySourceArtifactKind,
    PreparationKitV1,
)
from aieos.domains.education.worksheet_v1 import WorksheetV1
from aieos.platform.ai.fake import FakeStructuredModelGateway

pytestmark = pytest.mark.tos_dev04_i04


def _question(
    *,
    qid: str,
    objective_ids: list[str] | None = None,
    answer: str = "1/2",
    explanation: str | None = None,
    question_type: str = "short_answer",
) -> dict[str, object]:
    options = ["1/2", "1/3", "1/4"] if question_type == "multiple_choice" else []
    if options and answer not in options:
        answer = options[0]
    return {
        "id": qid,
        "prompt": f"Prompt for {qid}",
        "question_type": question_type,
        "difficulty": "easy",
        "bloom_level": "understand",
        "objective_ids": objective_ids or ["obj-1"],
        "options": options,
        "answer": answer,
        "explanation": explanation or f"Explanation for {qid}",
        "visual_description": None,
    }


def _worksheet_questions(count: int) -> list[dict[str, object]]:
    return [_question(qid=f"ws-{i + 1}", answer=f"ans-ws-{i + 1}") for i in range(count)]


def valid_kit_payload(*, worksheet_question_count: int = 6, **overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "title": "Photosynthesis preparation kit",
        "teacher_summary": "Coherent kit for tomorrow's class on photosynthesis.",
        "shared_learning_objectives": [
            {"id": "obj-1", "text": "Explain how plants make food"},
            {"id": "obj-2", "text": "Identify inputs and outputs of photosynthesis"},
        ],
        "lesson_plan": {
            "title": "Photosynthesis lesson",
            "objective_ids": ["obj-1", "obj-2"],
            "materials": ["Chart paper", "Leaf samples"],
            "opening": "Recall prior knowledge about plants.",
            "sections": [
                {
                    "id": "sec-1",
                    "title": "Explore",
                    "objective_ids": ["obj-1"],
                    "teacher_actions": "Demonstrate with visuals.",
                    "learner_actions": "Observe and discuss.",
                    "estimated_minutes": 10,
                }
            ],
            "closure": "Summarize the process.",
            "formative_check": "Thumbs up/down on key idea.",
        },
        "worksheet": {
            "title": "Photosynthesis worksheet",
            "instructions": "Answer all questions.",
            "questions": _worksheet_questions(worksheet_question_count),
        },
        "quick_quiz": {
            "title": "Exit quiz",
            "instructions": "Answer quickly.",
            "questions": [
                _question(qid="q1", answer="quiz-ans-1", explanation="Quiz explain 1"),
                _question(
                    qid="quiz-2",
                    objective_ids=["obj-2"],
                    answer="quiz-ans-2",
                    explanation="Quiz explain 2",
                ),
            ],
        },
        "homework": {
            "title": "Photosynthesis homework",
            "instructions": "Complete for next class.",
            "questions": [
                _question(
                    qid="hw-1",
                    objective_ids=["obj-2"],
                    answer="hw-ans-1",
                    explanation="HW explain 1",
                )
            ],
        },
        "teacher_notes": {
            "title": "Teacher preparation notes",
            "notes": ["Review leaf diagrams before class.", "Keep quiz under five minutes."],
        },
    }
    base.update(overrides)
    return base


def valid_kit(*, worksheet_question_count: int = 6, **overrides: object) -> PreparationKitV1:
    return PreparationKitV1.model_validate(
        valid_kit_payload(worksheet_question_count=worksheet_question_count, **overrides)
    )


class TestFinalPayloadBuilder:
    def test_builds_all_six_typed_payloads(self) -> None:
        kit = valid_kit()
        artifacts = build_preparation_artifact_payloads(kit)
        assert artifacts.lesson_plan.title == kit.lesson_plan.title
        assert artifacts.worksheet.title == kit.worksheet.title
        assert artifacts.quiz.title == kit.quick_quiz.title
        assert artifacts.homework.title == kit.homework.title
        assert artifacts.teacher_notes.title == kit.teacher_notes.title
        assert artifacts.answer_key.title == f"Answer Key — {kit.title}"

    def test_shared_objectives_copied_to_all_question_bearing_artifacts(self) -> None:
        kit = valid_kit()
        artifacts = build_preparation_artifact_payloads(kit)
        expected = [obj.model_dump() for obj in kit.shared_learning_objectives]
        assert [o.model_dump() for o in artifacts.lesson_plan.learning_objectives] == expected
        assert [o.model_dump() for o in artifacts.worksheet.learning_objectives] == expected
        assert [o.model_dump() for o in artifacts.quiz.learning_objectives] == expected
        assert [o.model_dump() for o in artifacts.homework.learning_objectives] == expected

    def test_worksheet_6_to_12_succeeds(self) -> None:
        for count in (6, 12):
            artifacts = build_preparation_artifact_payloads(
                valid_kit(worksheet_question_count=count)
            )
            assert len(artifacts.worksheet.questions) == count

    def test_worksheet_sub_6_fails_closed(self) -> None:
        kit = valid_kit(worksheet_question_count=3)
        assert isinstance(kit, PreparationKitV1)
        with pytest.raises(PreparationArtifactBuildFailed, match="worksheet"):
            build_preparation_artifact_payloads(kit)

    def test_worksheet_v1_contract_unchanged(self) -> None:
        field = WorksheetV1.model_fields["questions"]
        assert field.annotation is not None
        # Authoritative bounds remain 6–12
        meta = field.metadata
        assert any(getattr(m, "ge", None) == 6 or getattr(m, "min_length", None) == 6 for m in meta)
        assert any(getattr(m, "le", None) == 12 or getattr(m, "max_length", None) == 12 for m in meta)

    def test_mutable_alias_independence(self) -> None:
        kit = valid_kit()
        artifacts = build_preparation_artifact_payloads(kit)
        original_answer = artifacts.worksheet.questions[0].answer
        original_obj_text = artifacts.lesson_plan.learning_objectives[0].text
        original_notes = list(artifacts.teacher_notes.notes)

        kit.worksheet.questions[0].answer = "MUTATED-ANSWER"
        kit.shared_learning_objectives[0].text = "MUTATED-OBJECTIVE"
        kit.teacher_notes.notes[0] = "MUTATED-NOTE"

        assert artifacts.worksheet.questions[0].answer == original_answer
        assert artifacts.lesson_plan.learning_objectives[0].text == original_obj_text
        assert artifacts.teacher_notes.notes == original_notes


class TestAnswerKeyBuilder:
    def test_complete_matrix(self) -> None:
        kit = valid_kit()
        # Cross-artifact repeated question id q1 (worksheet also gets ws-* ids;
        # quiz already has q1; add matching worksheet id via rebuild).
        payload = valid_kit_payload()
        payload["worksheet"]["questions"][0]["id"] = "q1"  # type: ignore[index]
        payload["worksheet"]["questions"][0]["answer"] = "ws-q1-answer"  # type: ignore[index]
        payload["worksheet"]["questions"][0]["explanation"] = "ws-q1-explain"  # type: ignore[index]
        kit = PreparationKitV1.model_validate(payload)
        artifacts = build_preparation_artifact_payloads(kit)
        key = artifacts.answer_key

        expected_count = (
            len(artifacts.worksheet.questions)
            + len(artifacts.quiz.questions)
            + len(artifacts.homework.questions)
        )
        assert len(key.entries) == expected_count

        # Order: worksheet → quiz → homework; preserve source order
        cursor = 0
        for question in artifacts.worksheet.questions:
            entry = key.entries[cursor]
            assert entry.source_artifact_kind is AnswerKeySourceArtifactKind.WORKSHEET
            assert entry.source_question_id == question.id
            assert entry.answer == question.answer
            assert entry.explanation == question.explanation
            cursor += 1
        for question in artifacts.quiz.questions:
            entry = key.entries[cursor]
            assert entry.source_artifact_kind is AnswerKeySourceArtifactKind.QUIZ
            assert entry.source_question_id == question.id
            assert entry.answer == question.answer
            assert entry.explanation == question.explanation
            cursor += 1
        for question in artifacts.homework.questions:
            entry = key.entries[cursor]
            assert entry.source_artifact_kind is AnswerKeySourceArtifactKind.HOMEWORK
            assert entry.source_question_id == question.id
            assert entry.answer == question.answer
            assert entry.explanation == question.explanation
            cursor += 1

        # Repeated q1 across worksheet and quiz is legal and distinguished
        kinds_for_q1 = {
            e.source_artifact_kind
            for e in key.entries
            if e.source_question_id == "q1"
        }
        assert AnswerKeySourceArtifactKind.WORKSHEET in kinds_for_q1
        assert AnswerKeySourceArtifactKind.QUIZ in kinds_for_q1

    def test_deterministic_repeat_build(self) -> None:
        kit = valid_kit()
        a = build_preparation_artifact_payloads(kit).answer_key
        b = build_preparation_artifact_payloads(kit).answer_key
        assert a.model_dump() == b.model_dump()
        assert a.title == b.title == f"Answer Key — {kit.title}"

    def test_standalone_builder_no_gateway(self) -> None:
        kit = valid_kit()
        artifacts = build_preparation_artifact_payloads(kit)
        gateway = FakeStructuredModelGateway()
        key = build_answer_key_v1(
            kit_title=kit.title,
            worksheet=artifacts.worksheet,
            quiz=artifacts.quiz,
            homework=artifacts.homework,
        )
        assert gateway.call_count == 0
        assert key.title == f"Answer Key — {kit.title}"
        assert len(key.entries) == (
            len(artifacts.worksheet.questions)
            + len(artifacts.quiz.questions)
            + len(artifacts.homework.questions)
        )

    def test_provider_cannot_generate_answer_key_on_preparation_kit(self) -> None:
        payload = valid_kit_payload()
        payload["answer_key"] = {"title": "Key", "entries": []}
        with pytest.raises(ValidationError):
            PreparationKitV1.model_validate(payload)
        assert "answer_key" not in PreparationKitV1.model_fields

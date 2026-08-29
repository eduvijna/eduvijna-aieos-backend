"""TOS-DEV04-I03 final Content payload contract proofs."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from aieos.domains.education.content_payloads_v1 import (
    HomeworkV1,
    LessonPlanV1,
    QuizV1,
    TeacherNotesV1,
)
from aieos.domains.education.schema import (
    ANSWER_KEY_CONTENT_TYPE,
    ContentAudience,
    HOMEWORK_CONTENT_TYPE,
    LESSON_PLAN_CONTENT_TYPE,
    PREPARATION_ARTIFACT_AUDIENCE,
    PREPARATION_ARTIFACT_KINDS,
    QUIZ_CONTENT_TYPE,
    TEACHER_NOTES_CONTENT_TYPE,
    WORKSHEET_CONTENT_TYPE,
    build_preparation_content_schema_registry,
)

pytestmark = pytest.mark.tos_dev04_i03


def _objectives() -> list[dict[str, object]]:
    return [
        {"id": "obj-1", "text": "Identify fraction parts"},
        {"id": "obj-2", "text": "Compare simple fractions"},
    ]


def _question(
    *,
    qid: str = "q-1",
    objective_ids: list[str] | None = None,
    question_type: str = "short_answer",
) -> dict[str, object]:
    options = ["1/2", "1/3", "1/4"] if question_type == "multiple_choice" else []
    answer = options[0] if options else "1/2"
    return {
        "id": qid,
        "prompt": f"Prompt for {qid}",
        "question_type": question_type,
        "difficulty": "easy",
        "bloom_level": "understand",
        "objective_ids": objective_ids or ["obj-1"],
        "options": options,
        "answer": answer,
        "explanation": f"Explanation for {qid}",
        "visual_description": None,
    }


def valid_lesson_plan_payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "title": "Fractions lesson",
        "learning_objectives": _objectives(),
        "objective_ids": ["obj-1", "obj-2"],
        "materials": ["fraction tiles"],
        "opening": "Activate prior knowledge with a pizza model.",
        "sections": [
            {
                "id": "sec-1",
                "title": "Explore",
                "objective_ids": ["obj-1"],
                "teacher_actions": "Demonstrate halves and quarters.",
                "learner_actions": "Build models with tiles.",
                "estimated_minutes": 12,
            }
        ],
        "closure": "Summarize equivalent fractions.",
        "formative_check": "Ask one exit ticket question.",
    }
    base.update(overrides)
    return base


def valid_quiz_payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "title": "Fractions quiz",
        "learning_objectives": _objectives(),
        "instructions": "Answer independently.",
        "questions": [_question(qid="q-1"), _question(qid="q-2", objective_ids=["obj-2"])],
    }
    base.update(overrides)
    return base


def valid_homework_payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "title": "Fractions homework",
        "learning_objectives": _objectives(),
        "instructions": "Complete at home.",
        "questions": [_question(qid="h-1")],
    }
    base.update(overrides)
    return base


def valid_teacher_notes_payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "title": "Teacher notes",
        "notes": ["Watch for common half/quarter confusion."],
    }
    base.update(overrides)
    return base


class TestLessonPlanV1:
    def test_valid_accepted(self) -> None:
        LessonPlanV1.model_validate(valid_lesson_plan_payload())

    def test_unknown_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            LessonPlanV1.model_validate(
                valid_lesson_plan_payload(board_claim="CBSE")
            )

    def test_unknown_objective_mapping_rejected(self) -> None:
        with pytest.raises(ValidationError):
            LessonPlanV1.model_validate(
                valid_lesson_plan_payload(objective_ids=["obj-1", "missing"])
            )

    def test_duplicate_section_ids_rejected(self) -> None:
        section = {
            "id": "sec-1",
            "title": "Explore",
            "objective_ids": ["obj-1"],
            "teacher_actions": "Demonstrate.",
            "learner_actions": "Practice.",
            "estimated_minutes": 10,
        }
        with pytest.raises(ValidationError):
            LessonPlanV1.model_validate(
                valid_lesson_plan_payload(sections=[section, {**section, "title": "B"}])
            )


class TestQuizV1:
    def test_valid_accepted(self) -> None:
        QuizV1.model_validate(valid_quiz_payload())

    def test_unknown_objective_mapping_rejected(self) -> None:
        with pytest.raises(ValidationError):
            QuizV1.model_validate(
                valid_quiz_payload(
                    questions=[_question(objective_ids=["missing"])]
                )
            )

    def test_duplicate_question_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            QuizV1.model_validate(
                valid_quiz_payload(
                    questions=[_question(qid="q-1"), _question(qid="q-1")]
                )
            )

    def test_malformed_mcq_rejected(self) -> None:
        bad = _question(qid="q-1", question_type="multiple_choice")
        bad["options"] = ["only-one"]
        bad["answer"] = "only-one"
        with pytest.raises(ValidationError):
            QuizV1.model_validate(valid_quiz_payload(questions=[bad]))


class TestHomeworkV1:
    def test_valid_accepted(self) -> None:
        HomeworkV1.model_validate(valid_homework_payload())

    def test_unknown_objective_mapping_rejected(self) -> None:
        with pytest.raises(ValidationError):
            HomeworkV1.model_validate(
                valid_homework_payload(
                    questions=[_question(qid="h-1", objective_ids=["missing"])]
                )
            )

    def test_duplicate_question_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            HomeworkV1.model_validate(
                valid_homework_payload(
                    questions=[_question(qid="h-1"), _question(qid="h-1")]
                )
            )


class TestTeacherNotesV1:
    def test_valid_accepted(self) -> None:
        TeacherNotesV1.model_validate(valid_teacher_notes_payload())

    def test_whitespace_only_notes_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TeacherNotesV1.model_validate(
                valid_teacher_notes_payload(notes=["   "])
            )

    def test_unknown_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TeacherNotesV1.model_validate(
                valid_teacher_notes_payload(approval_hint="auto")
            )


class TestAudienceAndSchemas:
    def test_typed_audience_classification(self) -> None:
        assert PREPARATION_ARTIFACT_AUDIENCE["lesson_plan"] is ContentAudience.TEACHER
        assert PREPARATION_ARTIFACT_AUDIENCE["answer_key"] is ContentAudience.TEACHER
        assert PREPARATION_ARTIFACT_AUDIENCE["teacher_notes"] is ContentAudience.TEACHER
        assert PREPARATION_ARTIFACT_AUDIENCE["worksheet"] is ContentAudience.LEARNER
        assert PREPARATION_ARTIFACT_AUDIENCE["quiz"] is ContentAudience.LEARNER
        assert PREPARATION_ARTIFACT_AUDIENCE["homework"] is ContentAudience.LEARNER
        assert set(PREPARATION_ARTIFACT_AUDIENCE) == set(PREPARATION_ARTIFACT_KINDS)

    def test_preparation_registry_resolves_six_schemas(self) -> None:
        registry = build_preparation_content_schema_registry()
        expected = {
            LESSON_PLAN_CONTENT_TYPE: ("education.lesson_plan", 1),
            WORKSHEET_CONTENT_TYPE: ("education.worksheet", 1),
            QUIZ_CONTENT_TYPE: ("education.quiz", 1),
            HOMEWORK_CONTENT_TYPE: ("education.homework", 1),
            ANSWER_KEY_CONTENT_TYPE: ("education.answer_key", 1),
            TEACHER_NOTES_CONTENT_TYPE: ("education.teacher_notes", 1),
        }
        for content_type, (schema_id, version) in expected.items():
            registered = registry.get(schema_id, version)
            assert registered.content_type == content_type

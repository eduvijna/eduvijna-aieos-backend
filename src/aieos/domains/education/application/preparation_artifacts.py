"""Deterministic PreparationKitV1 → final Content payload transforms (TOS-DEV04-I04).

Pure builder: no gateway, database, time, or randomness.
"""

from __future__ import annotations

from pydantic import ValidationError

from aieos.domains.education.application.models import PreparationArtifactPayloadsV1
from aieos.domains.education.content_payloads_v1 import (
    AnswerKeyV1,
    HomeworkV1,
    LessonPlanV1,
    QuizV1,
    TeacherNotesV1,
)
from aieos.domains.education.preparation_kit_v1 import (
    AnswerKeyEntryV1,
    AnswerKeySourceArtifactKind,
    PreparationKitV1,
)
from aieos.domains.education.worksheet_v1 import (
    LearningObjectiveV1,
    WorksheetQuestionV1,
    WorksheetV1,
)


class PreparationArtifactBuildFailed(Exception):
    """Raised when PreparationKitV1 cannot convert to final governed payloads."""

    def __init__(self, message: str = "preparation artifact build failed") -> None:
        super().__init__(message)


def _answer_key_title(kit_title: str) -> str:
    """Deterministic title derived from the preparation kit title (no provider/time)."""
    return f"Answer Key — {kit_title}"


def _reconstruct_objectives(
    objectives: list[LearningObjectiveV1],
) -> list[LearningObjectiveV1]:
    return [
        LearningObjectiveV1.model_validate(obj.model_dump(mode="python"))
        for obj in objectives
    ]


def _reconstruct_questions(
    questions: list[WorksheetQuestionV1],
) -> list[WorksheetQuestionV1]:
    return [
        WorksheetQuestionV1.model_validate(question.model_dump(mode="python"))
        for question in questions
    ]


def build_answer_key_v1(
    *,
    kit_title: str,
    worksheet: WorksheetV1,
    quiz: QuizV1,
    homework: HomeworkV1,
) -> AnswerKeyV1:
    """Derive AnswerKeyV1 from final question-bearing payloads. No model call."""
    entries: list[AnswerKeyEntryV1] = []
    for question in worksheet.questions:
        entries.append(
            AnswerKeyEntryV1(
                source_artifact_kind=AnswerKeySourceArtifactKind.WORKSHEET,
                source_question_id=question.id,
                answer=question.answer,
                explanation=question.explanation,
            )
        )
    for question in quiz.questions:
        entries.append(
            AnswerKeyEntryV1(
                source_artifact_kind=AnswerKeySourceArtifactKind.QUIZ,
                source_question_id=question.id,
                answer=question.answer,
                explanation=question.explanation,
            )
        )
    for question in homework.questions:
        entries.append(
            AnswerKeyEntryV1(
                source_artifact_kind=AnswerKeySourceArtifactKind.HOMEWORK,
                source_question_id=question.id,
                answer=question.answer,
                explanation=question.explanation,
            )
        )
    expected = (
        len(worksheet.questions) + len(quiz.questions) + len(homework.questions)
    )
    if len(entries) != expected:
        raise PreparationArtifactBuildFailed("answer key entry count mismatch")
    try:
        return AnswerKeyV1.model_validate(
            {
                "title": _answer_key_title(kit_title),
                "entries": [entry.model_dump(mode="python") for entry in entries],
            }
        )
    except ValidationError as exc:
        raise PreparationArtifactBuildFailed("answer key validation failed") from exc


def build_preparation_artifact_payloads(
    kit: PreparationKitV1,
) -> PreparationArtifactPayloadsV1:
    """Transform provider envelope into six final typed Content payloads."""
    shared_objectives = _reconstruct_objectives(kit.shared_learning_objectives)

    try:
        lesson_plan = LessonPlanV1.model_validate(
            {
                "title": kit.lesson_plan.title,
                "learning_objectives": [
                    obj.model_dump(mode="python") for obj in shared_objectives
                ],
                "objective_ids": list(kit.lesson_plan.objective_ids),
                "materials": list(kit.lesson_plan.materials),
                "opening": kit.lesson_plan.opening,
                "sections": [
                    section.model_dump(mode="python")
                    for section in kit.lesson_plan.sections
                ],
                "closure": kit.lesson_plan.closure,
                "formative_check": kit.lesson_plan.formative_check,
            }
        )
    except ValidationError as exc:
        raise PreparationArtifactBuildFailed("lesson plan build failed") from exc

    try:
        worksheet = WorksheetV1.model_validate(
            {
                "title": kit.worksheet.title,
                "teacher_summary": kit.teacher_summary,
                "learning_objectives": [
                    obj.model_dump(mode="python") for obj in shared_objectives
                ],
                "instructions": kit.worksheet.instructions,
                "questions": [
                    q.model_dump(mode="python")
                    for q in _reconstruct_questions(kit.worksheet.questions)
                ],
                "teacher_notes": None,
            }
        )
    except ValidationError as exc:
        raise PreparationArtifactBuildFailed("worksheet build failed") from exc

    try:
        quiz = QuizV1.model_validate(
            {
                "title": kit.quick_quiz.title,
                "learning_objectives": [
                    obj.model_dump(mode="python") for obj in shared_objectives
                ],
                "instructions": kit.quick_quiz.instructions,
                "questions": [
                    q.model_dump(mode="python")
                    for q in _reconstruct_questions(kit.quick_quiz.questions)
                ],
            }
        )
    except ValidationError as exc:
        raise PreparationArtifactBuildFailed("quiz build failed") from exc

    try:
        homework = HomeworkV1.model_validate(
            {
                "title": kit.homework.title,
                "learning_objectives": [
                    obj.model_dump(mode="python") for obj in shared_objectives
                ],
                "instructions": kit.homework.instructions,
                "questions": [
                    q.model_dump(mode="python")
                    for q in _reconstruct_questions(kit.homework.questions)
                ],
            }
        )
    except ValidationError as exc:
        raise PreparationArtifactBuildFailed("homework build failed") from exc

    try:
        teacher_notes = TeacherNotesV1.model_validate(
            {
                "title": kit.teacher_notes.title,
                "notes": list(kit.teacher_notes.notes),
            }
        )
    except ValidationError as exc:
        raise PreparationArtifactBuildFailed("teacher notes build failed") from exc

    answer_key = build_answer_key_v1(
        kit_title=kit.title,
        worksheet=worksheet,
        quiz=quiz,
        homework=homework,
    )

    return PreparationArtifactPayloadsV1(
        lesson_plan=lesson_plan,
        worksheet=worksheet,
        quiz=quiz,
        homework=homework,
        answer_key=answer_key,
        teacher_notes=teacher_notes,
    )

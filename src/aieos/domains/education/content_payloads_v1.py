"""Final governed Content payload contracts for preparation artifacts (TOS-DEV04-I03).

PreparationKitV1 remains the provider-neutral generation envelope.
These models are the persisted Content payload contracts.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aieos.domains.education.preparation_kit_v1 import (
    AnswerKeyV1,
    LessonPlanSectionV1,
    _require_semantic_text,
    _require_semantic_text_list,
    _validate_preparation_questions,
)
from aieos.domains.education.worksheet_v1 import (
    LearningObjectiveV1,
    WorksheetQuestionV1,
    WorksheetV1,
)

__all__ = [
    "AnswerKeyV1",
    "HomeworkV1",
    "LessonPlanV1",
    "QuizV1",
    "TeacherNotesV1",
    "WorksheetV1",
]


def _validate_final_learning_objectives(
    objectives: list[LearningObjectiveV1],
) -> None:
    """Final Content payload boundary: reject whitespace-only objective id/text.

    Does not strip or rewrite values. Does not alter LearningObjectiveV1 /
    WorksheetV1 (DEV03 remains unchanged).
    """
    for objective in objectives:
        if not objective.id.strip():
            raise ValueError("learning objective id must not be blank")
        if not objective.text.strip():
            raise ValueError("learning objective text must not be blank")


class LessonPlanV1(BaseModel):
    """Self-contained lesson-plan Content payload (education.lesson_plan@1)."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    learning_objectives: list[LearningObjectiveV1] = Field(min_length=1)
    objective_ids: list[str] = Field(min_length=1)
    materials: list[str] = Field(min_length=1)
    opening: str = Field(min_length=1)
    sections: list[LessonPlanSectionV1] = Field(min_length=1)
    closure: str = Field(min_length=1)
    formative_check: str = Field(min_length=1)

    @field_validator("title", "opening", "closure", "formative_check")
    @classmethod
    def _semantic_strings(cls, value: str) -> str:
        return _require_semantic_text(value, label="value")

    @field_validator("objective_ids", "materials")
    @classmethod
    def _non_empty_strings(cls, value: list[str]) -> list[str]:
        return _require_semantic_text_list(value, label="value")

    @model_validator(mode="after")
    def _ids_and_mappings(self) -> LessonPlanV1:
        _validate_final_learning_objectives(self.learning_objectives)
        objective_ids = [obj.id for obj in self.learning_objectives]
        if len(objective_ids) != len(set(objective_ids)):
            raise ValueError("learning objective IDs must be unique")
        known = set(objective_ids)
        if any(oid not in known for oid in self.objective_ids):
            raise ValueError("every top-level objective reference must resolve")
        section_ids = [section.id for section in self.sections]
        if len(section_ids) != len(set(section_ids)):
            raise ValueError("lesson plan section IDs must be unique")
        for section in self.sections:
            if any(oid not in known for oid in section.objective_ids):
                raise ValueError("every section objective reference must resolve")
        return self


class QuizV1(BaseModel):
    """Self-contained quiz Content payload (education.quiz@1)."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    learning_objectives: list[LearningObjectiveV1] = Field(min_length=1)
    instructions: str = Field(min_length=1)
    questions: list[WorksheetQuestionV1] = Field(min_length=1)

    @field_validator("title", "instructions")
    @classmethod
    def _semantic_strings(cls, value: str) -> str:
        return _require_semantic_text(value, label="value")

    @model_validator(mode="after")
    def _ids_and_mappings(self) -> QuizV1:
        _validate_final_learning_objectives(self.learning_objectives)
        _validate_preparation_questions(self.questions, component="quiz")
        objective_ids = [obj.id for obj in self.learning_objectives]
        if len(objective_ids) != len(set(objective_ids)):
            raise ValueError("learning objective IDs must be unique")
        question_ids = [question.id for question in self.questions]
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("question IDs must be unique")
        known = set(objective_ids)
        for question in self.questions:
            if any(oid not in known for oid in question.objective_ids):
                raise ValueError("every question must map to valid objective IDs")
        return self


class HomeworkV1(BaseModel):
    """Self-contained homework Content payload (education.homework@1)."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    learning_objectives: list[LearningObjectiveV1] = Field(min_length=1)
    instructions: str = Field(min_length=1)
    questions: list[WorksheetQuestionV1] = Field(min_length=1)

    @field_validator("title", "instructions")
    @classmethod
    def _semantic_strings(cls, value: str) -> str:
        return _require_semantic_text(value, label="value")

    @model_validator(mode="after")
    def _ids_and_mappings(self) -> HomeworkV1:
        _validate_final_learning_objectives(self.learning_objectives)
        _validate_preparation_questions(self.questions, component="homework")
        objective_ids = [obj.id for obj in self.learning_objectives]
        if len(objective_ids) != len(set(objective_ids)):
            raise ValueError("learning objective IDs must be unique")
        question_ids = [question.id for question in self.questions]
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("question IDs must be unique")
        known = set(objective_ids)
        for question in self.questions:
            if any(oid not in known for oid in question.objective_ids):
                raise ValueError("every question must map to valid objective IDs")
        return self


class TeacherNotesV1(BaseModel):
    """Teacher-facing notes Content payload (education.teacher_notes@1)."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    notes: list[str] = Field(min_length=1)

    @field_validator("title")
    @classmethod
    def _semantic_title(cls, value: str) -> str:
        return _require_semantic_text(value, label="title")

    @field_validator("notes")
    @classmethod
    def _notes_nonempty(cls, value: list[str]) -> list[str]:
        return _require_semantic_text_list(value, label="notes")

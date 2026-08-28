"""WorksheetV1 structured educational payload (TOS-DEV03)."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class QuestionType(StrEnum):
    MULTIPLE_CHOICE = "multiple_choice"
    SHORT_ANSWER = "short_answer"
    TRUE_FALSE = "true_false"


class Difficulty(StrEnum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class BloomLevel(StrEnum):
    REMEMBER = "remember"
    UNDERSTAND = "understand"
    APPLY = "apply"
    ANALYZE = "analyze"
    EVALUATE = "evaluate"
    CREATE = "create"


class LearningObjectiveV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    text: str = Field(min_length=1)


class WorksheetQuestionV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    question_type: QuestionType
    difficulty: Difficulty
    bloom_level: BloomLevel
    objective_ids: list[str] = Field(min_length=1)
    options: list[str] = Field(default_factory=list)
    answer: str = Field(min_length=1)
    explanation: str = Field(min_length=1)
    visual_description: str | None = None

    @field_validator("objective_ids")
    @classmethod
    def _objective_ids_nonempty(cls, value: list[str]) -> list[str]:
        if any(not isinstance(item, str) or not item.strip() for item in value):
            raise ValueError("objective_ids entries must be non-empty strings")
        return value

    @model_validator(mode="after")
    def _options_and_answer(self) -> WorksheetQuestionV1:
        if self.question_type is QuestionType.MULTIPLE_CHOICE:
            if not (3 <= len(self.options) <= 5):
                raise ValueError("multiple_choice questions require 3–5 options")
            if any(not opt.strip() for opt in self.options):
                raise ValueError("multiple_choice options must be non-empty")
            if self.answer not in self.options:
                raise ValueError("multiple_choice answer must match one option")
        else:
            if self.options:
                raise ValueError("non-multiple-choice questions must have empty options")
        return self


class WorksheetV1(BaseModel):
    """Strict Worksheet payload for education.worksheet@1."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    teacher_summary: str = Field(min_length=1)
    learning_objectives: list[LearningObjectiveV1] = Field(min_length=1)
    instructions: str = Field(min_length=1)
    questions: list[WorksheetQuestionV1] = Field(min_length=6, max_length=12)
    teacher_notes: str | None = None

    @model_validator(mode="after")
    def _ids_and_mappings(self) -> WorksheetV1:
        objective_ids = [obj.id for obj in self.learning_objectives]
        if len(objective_ids) != len(set(objective_ids)):
            raise ValueError("learning objective IDs must be unique")
        question_ids = [q.id for q in self.questions]
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("question IDs must be unique")
        known = set(objective_ids)
        for question in self.questions:
            if any(oid not in known for oid in question.objective_ids):
                raise ValueError("every question must map to valid objective IDs")
        return self


WorksheetQuestionType = Literal["multiple_choice", "short_answer", "true_false"]

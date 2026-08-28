"""PreparationKitV1 provider-neutral structured generation contract (TOS-DEV04-I01)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aieos.domains.education.worksheet_v1 import (
    LearningObjectiveV1,
    WorksheetQuestionV1,
)


def _require_semantic_text(value: str, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must not be blank")
    return value


def _require_semantic_text_list(value: list[str], *, label: str) -> list[str]:
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{label} entries must not be blank")
    return value


def _validate_preparation_questions(
    questions: list[WorksheetQuestionV1],
    *,
    component: str,
) -> None:
    for question in questions:
        _require_semantic_text(question.id, label=f"{component} question id")
        _require_semantic_text(question.prompt, label=f"{component} question prompt")
        _require_semantic_text(question.answer, label=f"{component} question answer")
        _require_semantic_text(question.explanation, label=f"{component} question explanation")


class AnswerKeySourceArtifactKind(StrEnum):
    WORKSHEET = "worksheet"
    QUIZ = "quiz"
    HOMEWORK = "homework"


class LessonPlanSectionV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    objective_ids: list[str] = Field(min_length=1)
    teacher_actions: str = Field(min_length=1)
    learner_actions: str = Field(min_length=1)
    estimated_minutes: int = Field(ge=1)

    @field_validator("id", "title", "teacher_actions", "learner_actions")
    @classmethod
    def _semantic_strings(cls, value: str) -> str:
        return _require_semantic_text(value, label="value")

    @field_validator("objective_ids")
    @classmethod
    def _objective_ids_nonempty(cls, value: list[str]) -> list[str]:
        return _require_semantic_text_list(value, label="objective_ids")


class LessonPlanDraftV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
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
    def _unique_section_ids(self) -> LessonPlanDraftV1:
        section_ids = [section.id for section in self.sections]
        if len(section_ids) != len(set(section_ids)):
            raise ValueError("lesson plan section IDs must be unique")
        return self


class WorksheetDraftV1(BaseModel):
    """Preparation envelope worksheet draft — materializes to WorksheetV1 in later slices."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    instructions: str = Field(min_length=1)
    questions: list[WorksheetQuestionV1] = Field(min_length=1)

    @field_validator("title", "instructions")
    @classmethod
    def _semantic_strings(cls, value: str) -> str:
        return _require_semantic_text(value, label="value")

    @model_validator(mode="after")
    def _question_invariants(self) -> WorksheetDraftV1:
        _validate_preparation_questions(self.questions, component="worksheet")
        question_ids = [question.id for question in self.questions]
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("worksheet question IDs must be unique")
        return self


class QuickQuizDraftV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    instructions: str = Field(min_length=1)
    questions: list[WorksheetQuestionV1] = Field(min_length=1)

    @field_validator("title", "instructions")
    @classmethod
    def _semantic_strings(cls, value: str) -> str:
        return _require_semantic_text(value, label="value")

    @model_validator(mode="after")
    def _question_invariants(self) -> QuickQuizDraftV1:
        _validate_preparation_questions(self.questions, component="quiz")
        question_ids = [question.id for question in self.questions]
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("quiz question IDs must be unique")
        return self


class HomeworkDraftV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    instructions: str = Field(min_length=1)
    questions: list[WorksheetQuestionV1] = Field(min_length=1)

    @field_validator("title", "instructions")
    @classmethod
    def _semantic_strings(cls, value: str) -> str:
        return _require_semantic_text(value, label="value")

    @model_validator(mode="after")
    def _question_invariants(self) -> HomeworkDraftV1:
        _validate_preparation_questions(self.questions, component="homework")
        question_ids = [question.id for question in self.questions]
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("homework question IDs must be unique")
        return self


class TeacherNotesDraftV1(BaseModel):
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


class AnswerKeyEntryV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_artifact_kind: AnswerKeySourceArtifactKind
    source_question_id: str = Field(min_length=1)
    answer: str = Field(min_length=1)
    explanation: str = Field(min_length=1)

    @field_validator("source_question_id", "answer", "explanation")
    @classmethod
    def _semantic_strings(cls, value: str) -> str:
        return _require_semantic_text(value, label="value")


class AnswerKeyV1(BaseModel):
    """Governed answer-key payload contract (builder deferred to DEV04-I04)."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    entries: list[AnswerKeyEntryV1] = Field(min_length=1)

    @field_validator("title")
    @classmethod
    def _semantic_title(cls, value: str) -> str:
        return _require_semantic_text(value, label="title")

    @model_validator(mode="after")
    def _unique_source_refs(self) -> AnswerKeyV1:
        keys = [(entry.source_artifact_kind, entry.source_question_id) for entry in self.entries]
        if len(keys) != len(set(keys)):
            raise ValueError(
                "answer key entries must have unique (source_artifact_kind, source_question_id)"
            )
        return self


class PreparationKitV1(BaseModel):
    """Strict provider-neutral preparation kit structured output (no answer_key field)."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    teacher_summary: str = Field(min_length=1)
    shared_learning_objectives: list[LearningObjectiveV1] = Field(min_length=1)
    lesson_plan: LessonPlanDraftV1
    worksheet: WorksheetDraftV1
    quick_quiz: QuickQuizDraftV1
    homework: HomeworkDraftV1
    teacher_notes: TeacherNotesDraftV1

    @field_validator("title", "teacher_summary")
    @classmethod
    def _semantic_strings(cls, value: str) -> str:
        return _require_semantic_text(value, label="value")

    @field_validator("shared_learning_objectives")
    @classmethod
    def _semantic_shared_objectives(
        cls, value: list[LearningObjectiveV1]
    ) -> list[LearningObjectiveV1]:
        for objective in value:
            _require_semantic_text(objective.id, label="shared learning objective id")
            _require_semantic_text(objective.text, label="shared learning objective text")
        return value

    @model_validator(mode="after")
    def _cross_field_consistency(self) -> PreparationKitV1:
        objective_ids = [obj.id for obj in self.shared_learning_objectives]
        if len(objective_ids) != len(set(objective_ids)):
            raise ValueError("shared learning objective IDs must be unique")
        known = set(objective_ids)

        def _require_known(ids: list[str], *, label: str) -> None:
            if any(oid not in known for oid in ids):
                raise ValueError(f"{label} references unknown shared learning objective IDs")

        _require_known(self.lesson_plan.objective_ids, label="lesson_plan")
        for section in self.lesson_plan.sections:
            _require_known(section.objective_ids, label="lesson_plan section")

        for question in self.worksheet.questions:
            _require_known(question.objective_ids, label="worksheet question")
        for question in self.quick_quiz.questions:
            _require_known(question.objective_ids, label="quiz question")
        for question in self.homework.questions:
            _require_known(question.objective_ids, label="homework question")

        return self

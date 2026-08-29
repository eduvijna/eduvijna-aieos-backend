"""Education ContentSchema adapters and typed audience classification (TOS-DEV04-I03)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping

from pydantic import ValidationError

from aieos.domains.content.domain.errors import InvalidPayloadError
from aieos.domains.content.domain.schema import (
    ContentSchemaRegistry,
    SchemaId,
    SchemaVersion,
)
from aieos.domains.education.content_payloads_v1 import (
    AnswerKeyV1,
    HomeworkV1,
    LessonPlanV1,
    QuizV1,
    TeacherNotesV1,
)
from aieos.domains.education.worksheet_v1 import WorksheetV1

WORKSHEET_CONTENT_TYPE = "worksheet"
WORKSHEET_SCHEMA_ID = "education.worksheet"
WORKSHEET_SCHEMA_VERSION = 1

LESSON_PLAN_CONTENT_TYPE = "lesson_plan"
LESSON_PLAN_SCHEMA_ID = "education.lesson_plan"
LESSON_PLAN_SCHEMA_VERSION = 1

QUIZ_CONTENT_TYPE = "quiz"
QUIZ_SCHEMA_ID = "education.quiz"
QUIZ_SCHEMA_VERSION = 1

HOMEWORK_CONTENT_TYPE = "homework"
HOMEWORK_SCHEMA_ID = "education.homework"
HOMEWORK_SCHEMA_VERSION = 1

ANSWER_KEY_CONTENT_TYPE = "answer_key"
ANSWER_KEY_SCHEMA_ID = "education.answer_key"
ANSWER_KEY_SCHEMA_VERSION = 1

TEACHER_NOTES_CONTENT_TYPE = "teacher_notes"
TEACHER_NOTES_SCHEMA_ID = "education.teacher_notes"
TEACHER_NOTES_SCHEMA_VERSION = 1

PREPARATION_ARTIFACT_KINDS: tuple[str, ...] = (
    "lesson_plan",
    "worksheet",
    "quiz",
    "homework",
    "answer_key",
    "teacher_notes",
)

PREPARATION_CONTENT_TYPES: tuple[str, ...] = (
    LESSON_PLAN_CONTENT_TYPE,
    WORKSHEET_CONTENT_TYPE,
    QUIZ_CONTENT_TYPE,
    HOMEWORK_CONTENT_TYPE,
    ANSWER_KEY_CONTENT_TYPE,
    TEACHER_NOTES_CONTENT_TYPE,
)


class ContentAudience(StrEnum):
    """Baseline audience classification for preparation artifacts (policy metadata only)."""

    TEACHER = "teacher"
    LEARNER = "learner"


PREPARATION_ARTIFACT_AUDIENCE: Mapping[str, ContentAudience] = {
    "lesson_plan": ContentAudience.TEACHER,
    "worksheet": ContentAudience.LEARNER,
    "quiz": ContentAudience.LEARNER,
    "homework": ContentAudience.LEARNER,
    "answer_key": ContentAudience.TEACHER,
    "teacher_notes": ContentAudience.TEACHER,
}


def _raise_invalid(label: str, exc: ValidationError) -> None:
    raise InvalidPayloadError(f"{label} payload invalid: {exc.error_count()} error(s)") from exc


@dataclass(frozen=True, slots=True)
class WorksheetV1ContentSchema:
    """Validates worksheet payloads via WorksheetV1 (rejects unknown fields)."""

    content_type: str = WORKSHEET_CONTENT_TYPE
    schema_id: SchemaId = SchemaId(WORKSHEET_SCHEMA_ID)
    schema_version: SchemaVersion = SchemaVersion(WORKSHEET_SCHEMA_VERSION)

    def validate(self, payload: Mapping[str, object]) -> None:
        if not isinstance(payload, Mapping):
            raise InvalidPayloadError("worksheet payload must be a JSON object")
        try:
            WorksheetV1.model_validate(dict(payload))
        except ValidationError as exc:
            _raise_invalid("worksheet", exc)


@dataclass(frozen=True, slots=True)
class LessonPlanV1ContentSchema:
    content_type: str = LESSON_PLAN_CONTENT_TYPE
    schema_id: SchemaId = SchemaId(LESSON_PLAN_SCHEMA_ID)
    schema_version: SchemaVersion = SchemaVersion(LESSON_PLAN_SCHEMA_VERSION)

    def validate(self, payload: Mapping[str, object]) -> None:
        if not isinstance(payload, Mapping):
            raise InvalidPayloadError("lesson_plan payload must be a JSON object")
        try:
            LessonPlanV1.model_validate(dict(payload))
        except ValidationError as exc:
            _raise_invalid("lesson_plan", exc)


@dataclass(frozen=True, slots=True)
class QuizV1ContentSchema:
    content_type: str = QUIZ_CONTENT_TYPE
    schema_id: SchemaId = SchemaId(QUIZ_SCHEMA_ID)
    schema_version: SchemaVersion = SchemaVersion(QUIZ_SCHEMA_VERSION)

    def validate(self, payload: Mapping[str, object]) -> None:
        if not isinstance(payload, Mapping):
            raise InvalidPayloadError("quiz payload must be a JSON object")
        try:
            QuizV1.model_validate(dict(payload))
        except ValidationError as exc:
            _raise_invalid("quiz", exc)


@dataclass(frozen=True, slots=True)
class HomeworkV1ContentSchema:
    content_type: str = HOMEWORK_CONTENT_TYPE
    schema_id: SchemaId = SchemaId(HOMEWORK_SCHEMA_ID)
    schema_version: SchemaVersion = SchemaVersion(HOMEWORK_SCHEMA_VERSION)

    def validate(self, payload: Mapping[str, object]) -> None:
        if not isinstance(payload, Mapping):
            raise InvalidPayloadError("homework payload must be a JSON object")
        try:
            HomeworkV1.model_validate(dict(payload))
        except ValidationError as exc:
            _raise_invalid("homework", exc)


@dataclass(frozen=True, slots=True)
class AnswerKeyV1ContentSchema:
    content_type: str = ANSWER_KEY_CONTENT_TYPE
    schema_id: SchemaId = SchemaId(ANSWER_KEY_SCHEMA_ID)
    schema_version: SchemaVersion = SchemaVersion(ANSWER_KEY_SCHEMA_VERSION)

    def validate(self, payload: Mapping[str, object]) -> None:
        if not isinstance(payload, Mapping):
            raise InvalidPayloadError("answer_key payload must be a JSON object")
        try:
            AnswerKeyV1.model_validate(dict(payload))
        except ValidationError as exc:
            _raise_invalid("answer_key", exc)


@dataclass(frozen=True, slots=True)
class TeacherNotesV1ContentSchema:
    content_type: str = TEACHER_NOTES_CONTENT_TYPE
    schema_id: SchemaId = SchemaId(TEACHER_NOTES_SCHEMA_ID)
    schema_version: SchemaVersion = SchemaVersion(TEACHER_NOTES_SCHEMA_VERSION)

    def validate(self, payload: Mapping[str, object]) -> None:
        if not isinstance(payload, Mapping):
            raise InvalidPayloadError("teacher_notes payload must be a JSON object")
        try:
            TeacherNotesV1.model_validate(dict(payload))
        except ValidationError as exc:
            _raise_invalid("teacher_notes", exc)


WORKSHEET_V1_SCHEMA = WorksheetV1ContentSchema()
LESSON_PLAN_V1_SCHEMA = LessonPlanV1ContentSchema()
QUIZ_V1_SCHEMA = QuizV1ContentSchema()
HOMEWORK_V1_SCHEMA = HomeworkV1ContentSchema()
ANSWER_KEY_V1_SCHEMA = AnswerKeyV1ContentSchema()
TEACHER_NOTES_V1_SCHEMA = TeacherNotesV1ContentSchema()

PREPARATION_V1_SCHEMAS: tuple[object, ...] = (
    LESSON_PLAN_V1_SCHEMA,
    WORKSHEET_V1_SCHEMA,
    QUIZ_V1_SCHEMA,
    HOMEWORK_V1_SCHEMA,
    ANSWER_KEY_V1_SCHEMA,
    TEACHER_NOTES_V1_SCHEMA,
)


def build_preparation_content_schema_registry() -> ContentSchemaRegistry:
    """Explicit I03/test registry for the six preparation Content schemas.

    Does not activate schemas in production or development app composition.
    """
    registry = ContentSchemaRegistry()
    for schema in PREPARATION_V1_SCHEMAS:
        registry.register(schema)  # type: ignore[arg-type]
    return registry

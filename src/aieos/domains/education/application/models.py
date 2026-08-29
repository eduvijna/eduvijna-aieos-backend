"""Education application models for worksheet and preparation-kit generation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from aieos.domains.education.content_payloads_v1 import (
    AnswerKeyV1,
    HomeworkV1,
    LessonPlanV1,
    QuizV1,
    TeacherNotesV1,
)
from aieos.domains.education.preparation_kit_v1 import PreparationKitV1
from aieos.domains.education.worksheet_v1 import WorksheetV1
from aieos.platform.education.quality_baseline import EducationalQualityResult
from aieos.platform.resources import ResourceRef

WORK_RESOURCE_TYPE = "teaching.work"


def _require_semantic_text(value: str, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must not be blank")
    return value


def _require_optional_semantic_text(value: str | None, *, label: str) -> str | None:
    if value is None:
        return None
    return _require_semantic_text(value, label=label)


@dataclass(frozen=True, slots=True)
class WorksheetGenerationInput:
    work_ref: ResourceRef
    goal_text: str
    class_label: str | None
    subject: str | None
    topic: str | None
    target_date: date
    locale: str


@dataclass(frozen=True, slots=True)
class ProviderMetadata:
    provider_id: str
    model_id: str
    provider_response_id: str | None
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None


@dataclass(frozen=True, slots=True)
class WorksheetGenerationDraft:
    worksheet_payload: WorksheetV1
    provider_metadata: ProviderMetadata
    educational_quality_result: EducationalQualityResult


@dataclass(frozen=True, slots=True)
class PreparationKitGenerationInput:
    """Immutable typed input for education.generate_preparation_kit."""

    work_ref: ResourceRef
    goal_text: str
    class_label: str | None
    subject: str | None
    topic: str | None
    target_date: date
    locale: str

    def __post_init__(self) -> None:
        if not isinstance(self.work_ref, ResourceRef):
            raise ValueError("work_ref must be a ResourceRef")
        if self.work_ref.resource_type != WORK_RESOURCE_TYPE:
            raise ValueError("work_ref.resource_type must be teaching.work")
        if self.work_ref.resource_revision is None:
            raise ValueError("work_ref.resource_revision must be an exact non-null revision")
        object.__setattr__(
            self, "goal_text", _require_semantic_text(self.goal_text, label="goal_text")
        )
        object.__setattr__(
            self, "locale", _require_semantic_text(self.locale, label="locale")
        )
        object.__setattr__(
            self,
            "class_label",
            _require_optional_semantic_text(self.class_label, label="class_label"),
        )
        object.__setattr__(
            self,
            "subject",
            _require_optional_semantic_text(self.subject, label="subject"),
        )
        object.__setattr__(
            self,
            "topic",
            _require_optional_semantic_text(self.topic, label="topic"),
        )
        if not isinstance(self.target_date, date):
            raise ValueError("target_date must be a date")


@dataclass(frozen=True, slots=True)
class PreparationArtifactPayloadsV1:
    """In-memory typed final artifact payloads (not a durable aggregate)."""

    lesson_plan: LessonPlanV1
    worksheet: WorksheetV1
    quiz: QuizV1
    homework: HomeworkV1
    answer_key: AnswerKeyV1
    teacher_notes: TeacherNotesV1


@dataclass(frozen=True, slots=True)
class PreparationKitGenerationDraft:
    """In-memory generation draft before I05 quality / I06 orchestration."""

    preparation_kit: PreparationKitV1
    artifacts: PreparationArtifactPayloadsV1
    provider_metadata: ProviderMetadata

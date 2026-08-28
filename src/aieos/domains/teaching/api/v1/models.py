"""Pydantic HTTP DTOs for Teaching. Not domain entities and not database rows."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class TeachingWorkCreateRequest(BaseModel):
    """A Teaching Intent request. Accepting it creates a durable Work."""

    model_config = ConfigDict(extra="forbid")

    intent_type: str = Field(min_length=1)
    goal_text: str = Field(min_length=1, max_length=2000)
    target_date: date
    locale: str = Field(min_length=1, max_length=255)
    class_label: str | None = Field(default=None, max_length=255)
    subject: str | None = Field(default=None, max_length=255)
    topic: str | None = Field(default=None, max_length=255)


class TeachingWorkRefineRequest(BaseModel):
    """PATCH body. Omitted fields are unchanged; explicit null clears a field."""

    model_config = ConfigDict(extra="forbid")

    goal_text: str | None = Field(default=None, min_length=1, max_length=2000)
    target_date: date | None = None
    locale: str | None = Field(default=None, min_length=1, max_length=255)
    class_label: str | None = Field(default=None, max_length=255)
    subject: str | None = Field(default=None, max_length=255)
    topic: str | None = Field(default=None, max_length=255)


class TeachingWorkResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    work_id: UUID
    intent_type: str
    goal_text: str
    class_label: str | None
    subject: str | None
    topic: str | None
    target_date: date
    locale: str
    aggregate_revision: int
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None


class TeachingWorkListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[TeachingWorkResponse]
    has_more: bool


class EducationalQualityCheckResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    passed: bool
    explanation: str


class EducationalQualityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    checks: list[EducationalQualityCheckResponse]


class GeneratedArtifactResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_id: UUID
    version_id: UUID
    content_type: str
    title: str
    stewardship_state: str
    aggregate_revision: int


class TeachingWorkGenerateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    work_id: UUID
    generation_run_id: UUID
    artifact: GeneratedArtifactResponse
    educational_quality: EducationalQualityResponse


class WorkArtifactItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_id: UUID
    version_id: UUID
    content_type: str
    title: str
    origin: str
    stewardship_state: str
    aggregate_revision: int
    educational_quality: EducationalQualityResponse | None = None


class TeachingWorkArtifactsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    work_id: UUID
    items: list[WorkArtifactItemResponse]


class ReviewProjectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pending_count: int


class ContinueWorkSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    work_id: UUID
    intent_type: str
    goal_text: str
    class_label: str | None
    subject: str | None
    topic: str | None
    target_date: date
    aggregate_revision: int
    updated_at: datetime


class PreparationProjectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active_work_count: int
    continue_work: ContinueWorkSummaryResponse | None


class HeroActionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    work_id: UUID | None


class TeacherOsMissionResponse(BaseModel):
    """Derived projection. No mission row exists behind this response."""

    model_config = ConfigDict(extra="forbid")

    mission_date: date
    review: ReviewProjectionResponse
    preparation: PreparationProjectionResponse
    hero_action: HeroActionResponse

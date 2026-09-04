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


class RemediationTeachingWorkCreateRequest(BaseModel):
    """Strict Assessment-origin remediation request."""

    model_config = ConfigDict(extra="forbid")

    assessment_id: UUID
    expected_assessment_aggregate_revision: int = Field(ge=0)
    goal_text: str = Field(min_length=1, max_length=2000)
    target_date: date
    locale: str = Field(min_length=1, max_length=255)
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


class PreparationArtifactResponse(BaseModel):
    """One of the exact-six preparation Generic Content projections."""

    model_config = ConfigDict(extra="forbid")

    artifact_kind: str
    content_id: UUID
    version_id: UUID
    content_type: str
    title: str
    stewardship_state: str
    aggregate_revision: int
    generation_run_id: UUID


class PreparationStatusResponse(BaseModel):
    """Derived preparation status. No persisted preparation-status column."""

    model_config = ConfigDict(extra="forbid")

    status: str


class TeachingWorkGenerateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    work_id: UUID
    generation_run_id: UUID
    artifact: GeneratedArtifactResponse
    educational_quality: EducationalQualityResponse


class TeachingWorkPrepareResponse(BaseModel):
    """Additive prepare command response. Exact six artifacts required."""

    model_config = ConfigDict(extra="forbid")

    work_id: UUID
    generation_run_id: UUID
    preparation: PreparationStatusResponse
    artifacts: list[PreparationArtifactResponse]
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
    artifact_kind: str | None = None
    generation_run_id: UUID | None = None


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


class SchoolContextClassItemResponse(BaseModel):
    """Opaque ClassRef + display label. Not Class master data ownership."""

    model_config = ConfigDict(extra="forbid")

    class_ref: str
    display_label: str


class SchoolContextClassesResponse(BaseModel):
    """Current-authority assignable class list. Not durable CREATE authorization."""

    model_config = ConfigDict(extra="forbid")

    items: list[SchoolContextClassItemResponse]


class TeachingAssignmentCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_id: UUID
    content_version_id: UUID
    class_ref: str
    source_work_id: UUID | None = None
    available_from: datetime | None = None
    due_at: datetime | None = None


class TeachingAssignmentDueUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    due_at: datetime | None = None


class TeachingAssignmentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assignment_id: UUID
    teacher_principal_id: UUID
    content_id: UUID
    content_version_id: UUID
    audience_type: str
    class_ref: str
    audience_display_label: str | None
    source_work_id: UUID | None
    lifecycle_state: str
    assigned_at: datetime
    available_from: datetime
    due_at: datetime | None
    closed_at: datetime | None
    cancelled_at: datetime | None
    aggregate_revision: int
    created_at: datetime
    updated_at: datetime


class TeachingAssignmentListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[TeachingAssignmentResponse]
    has_more: bool


class TeachingExecutionContentBindingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_id: UUID
    content_version_id: UUID
    artifact_kind: str = Field(min_length=1)


class TeachingExecutionStartRequest(BaseModel):
    """Start request. Tenant/teacher/started_at/assignment_id are not client-settable."""

    model_config = ConfigDict(extra="forbid")

    work_id: UUID
    class_ref: str = Field(min_length=1)
    bindings: list[TeachingExecutionContentBindingRequest] = Field(default_factory=list)


class TeachingExecutionObservationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observation_kind: str = Field(min_length=1)
    body: str = Field(min_length=1)


class TeachingExecutionObservationCorrectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str = Field(min_length=1)


class TeachingExecutionContentBindingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_id: UUID
    content_version_id: UUID
    artifact_kind: str


class TeachingExecutionObservationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observation_id: UUID
    execution_id: UUID
    observation_kind: str
    body: str
    recorded_at: datetime
    updated_at: datetime
    revision: int


class TeachingExecutionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    execution_id: UUID
    teacher_principal_id: UUID
    work_id: UUID
    class_ref: str
    lifecycle_state: str
    started_at: datetime
    completed_at: datetime | None
    cancelled_at: datetime | None
    aggregate_revision: int
    created_at: datetime
    updated_at: datetime
    bindings: list[TeachingExecutionContentBindingResponse]
    observations: list[TeachingExecutionObservationResponse] = Field(
        default_factory=list
    )


class TeachingExecutionListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[TeachingExecutionResponse]
    has_more: bool


class TeacherOsTeachContextResponse(BaseModel):
    """Teacher OS Teach composition projection. No durable write behind this response."""

    model_config = ConfigDict(extra="forbid")

    work: ContinueWorkSummaryResponse
    class_ref: str
    display_label: str
    artifacts: list[WorkArtifactItemResponse]
    assignments: list[TeachingAssignmentResponse]
    executions: list[TeachingExecutionResponse]

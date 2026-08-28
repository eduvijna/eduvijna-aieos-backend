"""Education application models for worksheet generation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from aieos.domains.education.worksheet_v1 import WorksheetV1
from aieos.platform.education.quality_baseline import EducationalQualityResult
from aieos.platform.resources import ResourceRef


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

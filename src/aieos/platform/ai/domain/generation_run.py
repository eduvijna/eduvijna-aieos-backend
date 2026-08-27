"""GenerationRun aggregate — AI execution provenance, not Content truth."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Mapping
from uuid import UUID


class GenerationRunStatus(StrEnum):
    RUNNING = "RUNNING"
    VALIDATED = "VALIDATED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class InvalidGenerationRunError(ValueError):
    """Raised when GenerationRun invariants fail."""


def _require_uuid7(value: UUID, *, label: str) -> UUID:
    if not isinstance(value, UUID):
        raise InvalidGenerationRunError(f"{label} must be a UUID")
    if value.version != 7:
        raise InvalidGenerationRunError(
            f"{label} must be UUIDv7; got version {value.version!r}"
        )
    return value


@dataclass(frozen=True, slots=True)
class GenerationRunId:
    value: UUID

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "value", _require_uuid7(self.value, label="generation_run_id")
        )

    @classmethod
    def generate(cls) -> GenerationRunId:
        return cls(uuid.uuid7())

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True, slots=True)
class GenerationRun:
    """Durable AI execution record. Never stores prompts, raw output, or API keys."""

    generation_run_id: GenerationRunId
    tenant_id: UUID
    principal_id: UUID
    work_resource_type: str
    work_resource_id: UUID
    work_resource_revision: int
    capability_id: str
    provider_id: str
    model_id: str
    status: GenerationRunStatus
    request_fingerprint_sha256: str
    idempotency_key_sha256: str
    provider_response_id: str | None
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    educational_quality_summary: Mapping[str, object] | None
    result_content_id: UUID | None
    result_version_id: UUID | None
    result_content_revision: int | None
    failure_code: str | None
    aggregate_revision: int
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None

    def __post_init__(self) -> None:
        if not isinstance(self.generation_run_id, GenerationRunId):
            raise InvalidGenerationRunError("generation_run_id is required")
        if not isinstance(self.tenant_id, UUID) or not isinstance(self.principal_id, UUID):
            raise InvalidGenerationRunError("tenant_id and principal_id must be UUIDs")
        if self.work_resource_type != "teaching.work":
            raise InvalidGenerationRunError("work_resource_type must be teaching.work")
        if not isinstance(self.work_resource_id, UUID):
            raise InvalidGenerationRunError("work_resource_id must be a UUID")
        if (
            isinstance(self.work_resource_revision, bool)
            or not isinstance(self.work_resource_revision, int)
            or self.work_resource_revision < 0
        ):
            raise InvalidGenerationRunError("work_resource_revision must be non-negative")
        for label, value in (
            ("capability_id", self.capability_id),
            ("provider_id", self.provider_id),
            ("model_id", self.model_id),
            ("request_fingerprint_sha256", self.request_fingerprint_sha256),
            ("idempotency_key_sha256", self.idempotency_key_sha256),
        ):
            if not isinstance(value, str) or not value.strip():
                raise InvalidGenerationRunError(f"{label} must be a non-empty string")
        if not isinstance(self.status, GenerationRunStatus):
            raise InvalidGenerationRunError("status must be a GenerationRunStatus")
        if (
            isinstance(self.aggregate_revision, bool)
            or not isinstance(self.aggregate_revision, int)
            or self.aggregate_revision < 0
        ):
            raise InvalidGenerationRunError("aggregate_revision must be non-negative")
        if self.created_at.tzinfo is None or self.updated_at.tzinfo is None:
            raise InvalidGenerationRunError("timestamps must be timezone-aware")
        if self.updated_at < self.created_at:
            raise InvalidGenerationRunError("updated_at must be >= created_at")
        if self.completed_at is not None and self.completed_at.tzinfo is None:
            raise InvalidGenerationRunError("completed_at must be timezone-aware")

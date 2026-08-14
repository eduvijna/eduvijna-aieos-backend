"""Pydantic HTTP DTOs. Not domain entities and not database rows."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt


class ContentCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_type: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str
    locale: str = Field(min_length=1)


class ContentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_id: UUID
    content_type: str
    title: str
    description: str
    locale: str
    stewardship_state: str
    current_version_id: UUID | None
    published_version_id: UUID | None
    aggregate_revision: int
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None


class ContentListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ContentResponse]
    next_cursor: str | None


class ResourceRefRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resource_type: str
    resource_id: UUID
    resource_revision: Annotated[StrictInt | None, Field(default=None, ge=0)] = None


class VersionAssetRefRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resource_ref: ResourceRefRequest
    role: str
    ordinal: Annotated[StrictInt, Field(ge=0)]
    required: StrictBool


class ContentVersionAppendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_id: str = Field(min_length=1)
    schema_version: int = Field(ge=1)
    payload: dict[str, object]
    asset_refs: list[VersionAssetRefRequest] = Field(default_factory=list)


class ContentVersionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version_id: UUID
    content_id: UUID
    version_number: int
    parent_version_id: UUID | None
    schema_id: str
    schema_version: int
    payload: dict[str, object]
    payload_sha256: str
    origin: str
    created_at: datetime


class ReviewDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason_code: str | None = None
    comment: str | None = None


class ReviewSubmissionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_id: UUID
    version_id: UUID
    stewardship_state: str
    aggregate_revision: int


class ReviewDecisionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_decision_id: UUID
    content_id: UUID
    version_id: UUID
    decision: str
    reason_code: str | None
    comment: str | None
    decided_at: datetime
    stewardship_state: str
    aggregate_revision: int


class ContentPublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version_id: UUID


class PublicationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    publication_id: UUID
    content_id: UUID
    version_id: UUID
    approval_decision_id: UUID
    published_at: datetime
    published_version_id: UUID
    aggregate_revision: int

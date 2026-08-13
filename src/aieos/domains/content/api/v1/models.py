"""Pydantic HTTP DTOs. Not domain entities and not database rows."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


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


class ContentVersionAppendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_id: str = Field(min_length=1)
    schema_version: int = Field(ge=1)
    payload: dict[str, object]


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

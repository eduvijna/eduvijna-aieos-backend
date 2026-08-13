"""Idempotency scope and established outcomes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


CONTENT_CREATE_V1 = "content_create.v1"
CONTENT_VERSION_APPEND_V1 = "content_version_append.v1"


@dataclass(frozen=True, slots=True)
class IdempotencyScope:
    tenant_id: UUID
    principal_id: UUID
    operation: str
    key_sha256: str


@dataclass(frozen=True, slots=True)
class IdempotencyOutcome:
    tenant_id: UUID
    principal_id: UUID
    operation: str
    key_sha256: str
    request_fingerprint_sha256: str
    result_content_id: UUID
    result_version_id: UUID | None
    result_aggregate_revision: int
    created_at: datetime
    expires_at: datetime

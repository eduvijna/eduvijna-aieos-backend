"""Version-scoped immutable Content → Asset ResourceRef association."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from aieos.domains.content.domain.errors import InvalidVersionAssetRefError
from aieos.domains.content.domain.identities import (
    ContentId,
    ContentVersionId,
    require_foreign_uuid,
)
from aieos.platform.resources import ResourceRef

_ROLE_RE = re.compile(r"^[a-z][a-z0-9._-]{0,63}$")


@dataclass(frozen=True, slots=True)
class VersionAssetRef:
    """Immutable association between an exact ContentVersion and a ResourceRef."""

    tenant_id: UUID
    content_id: ContentId
    version_id: ContentVersionId
    resource_ref: ResourceRef
    role: str
    ordinal: int
    required: bool
    created_at: datetime

    def __post_init__(self) -> None:
        require_foreign_uuid(self.tenant_id, label="tenant_id")
        if not isinstance(self.resource_ref, ResourceRef):
            raise InvalidVersionAssetRefError("resource_ref must be a ResourceRef")
        if not isinstance(self.role, str) or not _ROLE_RE.fullmatch(self.role):
            raise InvalidVersionAssetRefError(
                "role must be a stable lowercase association code"
            )
        if isinstance(self.ordinal, bool) or not isinstance(self.ordinal, int):
            raise InvalidVersionAssetRefError("ordinal must be an integer >= 0")
        if self.ordinal < 0:
            raise InvalidVersionAssetRefError("ordinal must be an integer >= 0")
        if not isinstance(self.required, bool):
            raise InvalidVersionAssetRefError("required must be a boolean")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise InvalidVersionAssetRefError("created_at must be timezone-aware")

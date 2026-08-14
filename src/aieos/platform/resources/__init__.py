"""Framework-neutral cross-boundary ResourceRef contract."""

from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import UUID

_RESOURCE_TYPE_RE = re.compile(r"^[a-z][a-z0-9._-]{0,63}$")


class InvalidResourceRefError(ValueError):
    """Raised when a ResourceRef cannot be constructed."""


@dataclass(frozen=True, slots=True)
class ResourceRef:
    """Immutable reference to a foreign resource. Not authorization truth."""

    resource_type: str
    resource_id: UUID
    resource_revision: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.resource_type, str):
            raise InvalidResourceRefError("resource_type must be a string")
        if not _RESOURCE_TYPE_RE.fullmatch(self.resource_type):
            raise InvalidResourceRefError(
                "resource_type must be a stable lowercase identifier"
            )
        if not isinstance(self.resource_id, UUID):
            raise InvalidResourceRefError("resource_id must be a UUID")
        if self.resource_revision is not None:
            if isinstance(self.resource_revision, bool) or not isinstance(
                self.resource_revision, int
            ):
                raise InvalidResourceRefError(
                    "resource_revision must be NULL or a non-negative integer"
                )
            if self.resource_revision < 0:
                raise InvalidResourceRefError(
                    "resource_revision must be NULL or a non-negative integer"
                )

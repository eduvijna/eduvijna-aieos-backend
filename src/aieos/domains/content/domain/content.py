"""Content aggregate domain contract."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from aieos.domains.content.domain.errors import (
    InvalidContentAggregateError,
    InvalidContentTypeError,
)
from aieos.domains.content.domain.identities import (
    AggregateRevision,
    ContentId,
    ContentVersionId,
    require_foreign_uuid,
)
from aieos.domains.content.domain.states import StewardshipState, parse_stewardship_state


@dataclass(frozen=True, slots=True)
class ContentType:
    """Code-controlled content type name. Not an educational payload schema."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not self.value.strip():
            raise InvalidContentTypeError("content_type must be a non-empty string")
        object.__setattr__(self, "value", self.value.strip())

    def __str__(self) -> str:
        return self.value


def _require_aware(value: datetime, *, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise InvalidContentAggregateError(f"{label} must be timezone-aware")
    return value


def _require_text(value: str, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidContentAggregateError(f"{label} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True, slots=True)
class Content:
    """Pure-domain Content aggregate snapshot.

    published_version_id records a publication pointer. It is not a
    stewardship state of PUBLISHED.
    """

    content_id: ContentId
    tenant_id: UUID
    owner_principal_id: UUID
    content_type: ContentType
    title: str
    description: str
    locale: str
    stewardship_state: StewardshipState
    current_version_id: ContentVersionId | None
    published_version_id: ContentVersionId | None
    aggregate_revision: AggregateRevision
    created_at: datetime
    created_by_principal_id: UUID
    updated_at: datetime
    archived_at: datetime | None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "stewardship_state", parse_stewardship_state(self.stewardship_state)
        )
        object.__setattr__(self, "title", _require_text(self.title, label="title"))
        if not isinstance(self.description, str):
            raise InvalidContentAggregateError("description must be a string")
        object.__setattr__(self, "locale", _require_text(self.locale, label="locale"))
        require_foreign_uuid(self.tenant_id, label="tenant_id")
        require_foreign_uuid(self.owner_principal_id, label="owner_principal_id")
        require_foreign_uuid(
            self.created_by_principal_id, label="created_by_principal_id"
        )
        _require_aware(self.created_at, label="created_at")
        _require_aware(self.updated_at, label="updated_at")
        if self.archived_at is not None:
            _require_aware(self.archived_at, label="archived_at")

        if self.stewardship_state is StewardshipState.ARCHIVED:
            if self.archived_at is None:
                raise InvalidContentAggregateError(
                    "archived_at is required when stewardship_state is ARCHIVED"
                )
            if self.published_version_id is not None:
                raise InvalidContentAggregateError(
                    "ARCHIVED Content must withdraw the active published_version_id; "
                    "historical Publication facts are unchanged"
                )
        elif self.archived_at is not None:
            raise InvalidContentAggregateError(
                "archived_at is only valid when stewardship_state is ARCHIVED"
            )

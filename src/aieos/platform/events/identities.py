"""Event and outbox identity value objects. UUIDv7 only."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from uuid import UUID


def _require_uuid7(value: UUID | str, *, label: str) -> UUID:
    if isinstance(value, UUID):
        parsed = value
    elif isinstance(value, str):
        parsed = UUID(value)
    else:
        raise ValueError(f"{label} must be a UUID")
    if parsed.version != 7:
        raise ValueError(f"{label} must be UUIDv7; got version {parsed.version!r}")
    return parsed


@dataclass(frozen=True, slots=True)
class EventId:
    value: UUID

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _require_uuid7(self.value, label="event_id"))

    @classmethod
    def generate(cls) -> EventId:
        return cls(uuid.uuid7())

    def __str__(self) -> str:
        return str(self.value)

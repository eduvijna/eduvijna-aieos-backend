"""Security mutation-audit identity value objects. UUIDv7 only."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from uuid import UUID

from aieos.platform.security.audit.errors import InvalidSecurityAuditError


def _require_uuid7(value: UUID | str, *, label: str) -> UUID:
    if isinstance(value, UUID):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = UUID(value)
        except ValueError as exc:
            raise InvalidSecurityAuditError(f"{label} is not a valid UUID") from exc
    else:
        raise InvalidSecurityAuditError(f"{label} must be a UUID")
    if parsed.version != 7:
        raise InvalidSecurityAuditError(
            f"{label} must be UUIDv7; got version {parsed.version!r}"
        )
    return parsed


@dataclass(frozen=True, slots=True)
class AuditRecordId:
    """Identity of one immutable SecurityMutationAuditRecord."""

    value: UUID

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "value", _require_uuid7(self.value, label="audit_record_id")
        )

    @classmethod
    def generate(cls) -> AuditRecordId:
        return cls(uuid.uuid7())

    def __str__(self) -> str:
        return str(self.value)

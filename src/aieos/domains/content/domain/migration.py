"""Migration source identity and mapping identifiers (GCI-I13). Framework-neutral."""

from __future__ import annotations

import re
from dataclasses import dataclass

from aieos.domains.content.domain.errors import InvalidMigrationSourceIdentityError

_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9._-]{0,63}$")
_SOURCE_RESOURCE_ID_MAX = 255
_SOURCE_VERSION_MAX = 255
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


def require_migration_identifier(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
        raise InvalidMigrationSourceIdentityError(
            f"{label} must be a stable lowercase identifier"
        )
    return value


def require_source_resource_id(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise InvalidMigrationSourceIdentityError(
            "source_resource_id must be a non-empty string"
        )
    if len(value) > _SOURCE_RESOURCE_ID_MAX:
        raise InvalidMigrationSourceIdentityError(
            "source_resource_id must be at most 255 characters"
        )
    if value != value.strip():
        raise InvalidMigrationSourceIdentityError(
            "source_resource_id must not have leading or trailing whitespace"
        )
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise InvalidMigrationSourceIdentityError(
            "source_resource_id must not contain control characters"
        )
    return value


def require_optional_source_version(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise InvalidMigrationSourceIdentityError(
            "source_version must be a non-empty string when present"
        )
    if len(value) > _SOURCE_VERSION_MAX:
        raise InvalidMigrationSourceIdentityError(
            "source_version must be at most 255 characters"
        )
    if value != value.strip():
        raise InvalidMigrationSourceIdentityError(
            "source_version must not have leading or trailing whitespace"
        )
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise InvalidMigrationSourceIdentityError(
            "source_version must not contain control characters"
        )
    return value


def require_source_digest_sha256(value: object) -> str:
    if not isinstance(value, str) or not _DIGEST_RE.fullmatch(value):
        raise InvalidMigrationSourceIdentityError(
            "source_digest_sha256 must be 64 lowercase hexadecimal characters"
        )
    return value


def require_mapping_version(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidMigrationSourceIdentityError(
            "mapping_version must be a strict integer >= 1"
        )
    if value < 1:
        raise InvalidMigrationSourceIdentityError(
            "mapping_version must be a strict integer >= 1"
        )
    return value


@dataclass(frozen=True, slots=True)
class MigrationSourceIdentity:
    """Stable external source identity. Never becomes Content/Version identity."""

    source_system: str
    source_resource_type: str
    source_resource_id: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_system",
            require_migration_identifier(self.source_system, label="source_system"),
        )
        object.__setattr__(
            self,
            "source_resource_type",
            require_migration_identifier(
                self.source_resource_type, label="source_resource_type"
            ),
        )
        object.__setattr__(
            self,
            "source_resource_id",
            require_source_resource_id(self.source_resource_id),
        )

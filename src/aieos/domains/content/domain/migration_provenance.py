"""Typed migration import provenance V1 (GCI-I13). Framework-neutral."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping
from uuid import UUID

from aieos.domains.content.domain.errors import InvalidMigrationImportProvenanceError
from aieos.domains.content.domain.migration import (
    require_mapping_version,
    require_migration_identifier,
    require_optional_source_version,
    require_source_digest_sha256,
    require_source_resource_id,
)

MIGRATION_IMPORT_PROVENANCE_KIND = "migration_import"
MIGRATION_IMPORT_PROVENANCE_SCHEMA_VERSION = 1

_TOP_LEVEL_KEYS = (
    "kind",
    "schema_version",
    "migration_batch_id",
    "source_system",
    "source_resource_type",
    "source_resource_id",
    "source_version",
    "source_digest_sha256",
    "mapping_id",
    "mapping_version",
)
_TOP_LEVEL_KEY_SET = frozenset(_TOP_LEVEL_KEYS)


@dataclass(frozen=True, slots=True)
class MigrationImportProvenanceV1:
    """Allow-listed IMPORT provenance. Not authorization or stewardship truth."""

    migration_batch_id: UUID
    source_system: str
    source_resource_type: str
    source_resource_id: str
    source_version: str | None
    source_digest_sha256: str
    mapping_id: str
    mapping_version: int

    def __post_init__(self) -> None:
        try:
            if not isinstance(self.migration_batch_id, UUID):
                raise InvalidMigrationImportProvenanceError(
                    "migration_batch_id must be a UUID"
                )
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
            object.__setattr__(
                self,
                "source_version",
                require_optional_source_version(self.source_version),
            )
            object.__setattr__(
                self,
                "source_digest_sha256",
                require_source_digest_sha256(self.source_digest_sha256),
            )
            object.__setattr__(
                self,
                "mapping_id",
                require_migration_identifier(self.mapping_id, label="mapping_id"),
            )
            object.__setattr__(
                self,
                "mapping_version",
                require_mapping_version(self.mapping_version),
            )
        except InvalidMigrationImportProvenanceError:
            raise
        except Exception as exc:
            from aieos.domains.content.domain.errors import (
                InvalidMigrationSourceIdentityError,
            )

            if isinstance(exc, InvalidMigrationSourceIdentityError):
                raise InvalidMigrationImportProvenanceError(str(exc)) from exc
            raise

    @property
    def kind(self) -> str:
        return MIGRATION_IMPORT_PROVENANCE_KIND

    @property
    def schema_version(self) -> int:
        return MIGRATION_IMPORT_PROVENANCE_SCHEMA_VERSION


def migration_import_provenance_as_json(
    provenance: MigrationImportProvenanceV1,
) -> dict[str, object]:
    """Deterministic allow-listed serialization. No unknown keys."""
    return {
        "kind": provenance.kind,
        "schema_version": provenance.schema_version,
        "migration_batch_id": str(provenance.migration_batch_id),
        "source_system": provenance.source_system,
        "source_resource_type": provenance.source_resource_type,
        "source_resource_id": provenance.source_resource_id,
        "source_version": provenance.source_version,
        "source_digest_sha256": provenance.source_digest_sha256,
        "mapping_id": provenance.mapping_id,
        "mapping_version": provenance.mapping_version,
    }


def migration_import_provenance_from_json(
    value: Mapping[str, Any] | Mapping[str, object],
) -> MigrationImportProvenanceV1:
    """Strict parser. Rejects unknown top-level keys and secret-shaped extras."""
    if not isinstance(value, Mapping):
        raise InvalidMigrationImportProvenanceError("provenance must be a JSON object")
    keys = set(value.keys())
    if keys != _TOP_LEVEL_KEY_SET:
        unexpected = sorted(keys - _TOP_LEVEL_KEY_SET)
        missing = sorted(_TOP_LEVEL_KEY_SET - keys)
        if unexpected:
            raise InvalidMigrationImportProvenanceError(
                f"unexpected provenance field: {unexpected[0]}"
            )
        raise InvalidMigrationImportProvenanceError(
            f"missing provenance field: {missing[0]}"
        )
    if value["kind"] != MIGRATION_IMPORT_PROVENANCE_KIND:
        raise InvalidMigrationImportProvenanceError("kind must be migration_import")
    schema_version = value["schema_version"]
    if isinstance(schema_version, bool) or not isinstance(schema_version, int):
        raise InvalidMigrationImportProvenanceError(
            "schema_version must be the integer 1"
        )
    if schema_version != MIGRATION_IMPORT_PROVENANCE_SCHEMA_VERSION:
        raise InvalidMigrationImportProvenanceError("schema_version must be 1")
    batch_raw = value["migration_batch_id"]
    try:
        if isinstance(batch_raw, UUID):
            migration_batch_id = batch_raw
        elif isinstance(batch_raw, str):
            migration_batch_id = UUID(batch_raw)
        else:
            raise ValueError("not a UUID")
    except (TypeError, ValueError) as exc:
        raise InvalidMigrationImportProvenanceError(
            "migration_batch_id must be a UUID"
        ) from exc
    try:
        return MigrationImportProvenanceV1(
            migration_batch_id=migration_batch_id,
            source_system=value["source_system"],  # type: ignore[arg-type]
            source_resource_type=value["source_resource_type"],  # type: ignore[arg-type]
            source_resource_id=value["source_resource_id"],  # type: ignore[arg-type]
            source_version=value["source_version"],  # type: ignore[arg-type]
            source_digest_sha256=value["source_digest_sha256"],  # type: ignore[arg-type]
            mapping_id=value["mapping_id"],  # type: ignore[arg-type]
            mapping_version=value["mapping_version"],  # type: ignore[arg-type]
        )
    except InvalidMigrationImportProvenanceError:
        raise
    except Exception as exc:
        from aieos.domains.content.domain.errors import (
            InvalidMigrationSourceIdentityError,
        )

        if isinstance(exc, InvalidMigrationSourceIdentityError):
            raise InvalidMigrationImportProvenanceError(str(exc)) from exc
        raise InvalidMigrationImportProvenanceError(
            "migration import provenance is invalid"
        ) from exc

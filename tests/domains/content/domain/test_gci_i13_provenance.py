"""GCI-I13 domain migration identity and IMPORT provenance contracts."""

from __future__ import annotations

import uuid

import pytest

from aieos.domains.content.domain.errors import (
    InvalidMigrationImportProvenanceError,
    InvalidMigrationSourceIdentityError,
)
from aieos.domains.content.domain.migration import (
    MigrationSourceIdentity,
    require_mapping_version,
    require_migration_identifier,
    require_optional_source_version,
    require_source_digest_sha256,
    require_source_resource_id,
)
from aieos.domains.content.domain.migration_provenance import (
    MigrationImportProvenanceV1,
    migration_import_provenance_as_json,
    migration_import_provenance_from_json,
)

pytestmark = pytest.mark.gci_i13

DIGEST = "a" * 64


def _valid_provenance(**overrides) -> MigrationImportProvenanceV1:
    base = dict(
        migration_batch_id=uuid.uuid7(),
        source_system="legacy.edu",
        source_resource_type="lesson",
        source_resource_id="42",
        source_version="v3",
        source_digest_sha256=DIGEST,
        mapping_id="edu.lesson.v1",
        mapping_version=1,
    )
    base.update(overrides)
    return MigrationImportProvenanceV1(**base)


class TestMigrationSourceIdentity:
    def test_immutable_and_preserves_opaque_id(self) -> None:
        identity = MigrationSourceIdentity("legacy.edu", "lesson", "AbC-42")
        assert identity.source_resource_id == "AbC-42"
        with pytest.raises(AttributeError):
            identity.source_system = "other"  # type: ignore[misc]

    @pytest.mark.parametrize("value", ["", "Bad", "UPPER", "1bad", "a" * 65])
    def test_source_system_rejected(self, value: str) -> None:
        with pytest.raises(InvalidMigrationSourceIdentityError):
            require_migration_identifier(value, label="source_system")

    @pytest.mark.parametrize("value", ["", "BadType", "1x"])
    def test_source_resource_type_rejected(self, value: str) -> None:
        with pytest.raises(InvalidMigrationSourceIdentityError):
            require_migration_identifier(value, label="source_resource_type")

    def test_source_resource_id_contract(self) -> None:
        assert require_source_resource_id("x") == "x"
        with pytest.raises(InvalidMigrationSourceIdentityError):
            require_source_resource_id(" leading")
        with pytest.raises(InvalidMigrationSourceIdentityError):
            require_source_resource_id("a\nb")
        with pytest.raises(InvalidMigrationSourceIdentityError):
            require_source_resource_id("a" * 256)

    def test_optional_source_version(self) -> None:
        assert require_optional_source_version(None) is None
        assert require_optional_source_version("rev-1") == "rev-1"
        with pytest.raises(InvalidMigrationSourceIdentityError):
            require_optional_source_version("")

    def test_digest_lowercase_sha256(self) -> None:
        assert require_source_digest_sha256(DIGEST) == DIGEST
        with pytest.raises(InvalidMigrationSourceIdentityError):
            require_source_digest_sha256("A" * 64)
        with pytest.raises(InvalidMigrationSourceIdentityError):
            require_source_digest_sha256("a" * 63)

    def test_mapping_version_strict_int(self) -> None:
        assert require_mapping_version(1) == 1
        with pytest.raises(InvalidMigrationSourceIdentityError):
            require_mapping_version(True)
        with pytest.raises(InvalidMigrationSourceIdentityError):
            require_mapping_version(0)


class TestMigrationImportProvenanceV1:
    def test_round_trip(self) -> None:
        original = _valid_provenance(source_version=None)
        restored = migration_import_provenance_from_json(
            migration_import_provenance_as_json(original)
        )
        assert restored == original
        assert restored.kind == "migration_import"
        assert restored.schema_version == 1

    def test_exact_allow_list(self) -> None:
        payload = migration_import_provenance_as_json(_valid_provenance())
        assert set(payload) == {
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
        }

    @pytest.mark.parametrize(
        "mutate",
        [
            lambda d: d.pop("mapping_id"),
            lambda d: d.__setitem__("extra", "nope"),
            lambda d: d.__setitem__("kind", "other"),
            lambda d: d.__setitem__("schema_version", 2),
            lambda d: d.__setitem__("schema_version", True),
            lambda d: d.__setitem__("schema_version", 1.0),
            lambda d: d.__setitem__("schema_version", "1"),
            lambda d: d.__setitem__("mapping_version", True),
            lambda d: d.__setitem__("mapping_version", 0),
            lambda d: d.__setitem__("source_digest_sha256", "Z" * 64),
            lambda d: d.__setitem__("migration_batch_id", "not-uuid"),
        ],
    )
    def test_parser_rejects_invalid_shapes(self, mutate) -> None:
        payload = migration_import_provenance_as_json(_valid_provenance())
        mutate(payload)
        with pytest.raises(InvalidMigrationImportProvenanceError):
            migration_import_provenance_from_json(payload)

    @pytest.mark.parametrize(
        "secret_key",
        ["api_key", "access_token", "password", "authorization_header", "client_secret"],
    )
    def test_secret_fields_rejected(self, secret_key: str) -> None:
        payload = migration_import_provenance_as_json(_valid_provenance())
        payload[secret_key] = "SECRET"
        with pytest.raises(InvalidMigrationImportProvenanceError):
            migration_import_provenance_from_json(payload)

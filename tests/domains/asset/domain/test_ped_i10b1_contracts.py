"""PED-I10B1 Asset domain contract and architecture guard tests."""

from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4, uuid7

import pytest

from aieos.domains.asset.domain.asset import Asset
from aieos.domains.asset.domain.errors import (
    InvalidAssetAggregateRevisionError,
    InvalidAssetError,
    InvalidAssetIdentityError,
    InvalidAssetResourceTypeError,
    InvalidAssetRevisionError,
    InvalidAssetRevisionNumberError,
    InvalidAssetRevisionStateError,
    InvalidAssetStateError,
)
from aieos.domains.asset.domain.identities import (
    AssetAggregateRevision,
    AssetId,
    AssetRevisionId,
    AssetRevisionNumber,
)
from aieos.domains.asset.domain.resource_type import (
    ASSET_RESOURCE_TYPES_V1,
    AssetResourceType,
    parse_asset_resource_type,
)
from aieos.domains.asset.domain.revision import AssetRevision, AssetRevisionState
from aieos.domains.asset.domain.state import (
    AssetLifecycle,
    AssetQuarantineState,
    AssetRevisionSafetyState,
    parse_asset_lifecycle,
    parse_asset_quarantine_state,
    parse_asset_revision_safety_state,
)
from aieos.platform.resources import ResourceRef
from tests.dbutil import REPO_ROOT

pytestmark = pytest.mark.ped_i10b1

ASSET_DOMAIN = REPO_ROOT / "src" / "aieos" / "domains" / "asset"
MIGRATIONS = REPO_ROOT / "migrations" / "versions"
COMPOSITION = REPO_ROOT / "src" / "aieos" / "platform" / "runtime" / "composition.py"
OPENAPI = REPO_ROOT / "contracts" / "openapi" / "aieos-v1.json"

_SHA256_OK = "a" * 64
_SHA256_UPPER = "A" * 64

_FORBIDDEN_IMPORT_ROOTS = frozenset(
    {
        "fastapi",
        "sqlalchemy",
        "alembic",
        "temporalio",
        "nats",
        "boto3",
        "botocore",
        "azure",
        "google",
        "minio",
    }
)


def _now() -> datetime:
    return datetime(2026, 8, 16, 12, 0, tzinfo=UTC)


def _asset(**overrides: object) -> Asset:
    values: dict[str, object] = {
        "tenant_id": uuid7(),
        "asset_id": AssetId.generate(),
        "resource_type": AssetResourceType.IMAGE,
        "lifecycle": AssetLifecycle.ACTIVE,
        "quarantine_state": AssetQuarantineState.CLEAR,
        "current_revision": None,
        "aggregate_revision": AssetAggregateRevision(0),
        "created_at": _now(),
        "created_by_principal_id": uuid7(),
    }
    values.update(overrides)
    return Asset(**values)  # type: ignore[arg-type]


def _revision(**overrides: object) -> AssetRevision:
    values: dict[str, object] = {
        "tenant_id": uuid7(),
        "asset_id": AssetId.generate(),
        "asset_revision_id": AssetRevisionId.generate(),
        "revision_number": AssetRevisionNumber(1),
        "resource_type": AssetResourceType.DOCUMENT,
        "storage_key": "opaque/key/1",
        "media_type": "application/pdf",
        "byte_size": 0,
        "sha256": _SHA256_OK,
        "created_at": _now(),
        "created_by_principal_id": uuid7(),
    }
    values.update(overrides)
    return AssetRevision(**values)  # type: ignore[arg-type]


def _revision_state(**overrides: object) -> AssetRevisionState:
    values: dict[str, object] = {
        "tenant_id": uuid7(),
        "asset_id": AssetId.generate(),
        "asset_revision_id": AssetRevisionId.generate(),
        "revision_number": AssetRevisionNumber(1),
        "safety_state": AssetRevisionSafetyState.PENDING,
        "bytes_purged": False,
        "updated_at": _now(),
    }
    values.update(overrides)
    return AssetRevisionState(**values)  # type: ignore[arg-type]


class TestIdentities:
    def test_asset_and_revision_ids_are_uuid7(self) -> None:
        asset_id = AssetId.generate()
        revision_id = AssetRevisionId.generate()
        assert asset_id.value.version == 7
        assert revision_id.value.version == 7
        assert str(asset_id) == str(asset_id.value)
        assert AssetId(str(asset_id.value)).value == asset_id.value

    def test_uuid4_rejected_for_asset_owned_ids(self) -> None:
        with pytest.raises(InvalidAssetIdentityError):
            AssetId(uuid4())
        with pytest.raises(InvalidAssetIdentityError):
            AssetRevisionId(uuid4())

    @pytest.mark.parametrize("bad", [0, -1, True, False, 1.5, "1", None])
    def test_revision_number_rejects_invalid(self, bad: object) -> None:
        with pytest.raises(InvalidAssetRevisionNumberError):
            AssetRevisionNumber(bad)  # type: ignore[arg-type]

    def test_revision_number_positive_and_next(self) -> None:
        first = AssetRevisionNumber(1)
        assert int(first) == 1
        assert first.next() == AssetRevisionNumber(2)

    def test_aggregate_revision_accepts_zero(self) -> None:
        assert AssetAggregateRevision(0).value == 0
        assert int(AssetAggregateRevision(3)) == 3

    @pytest.mark.parametrize("bad", [-1, True, False, 1.0, "0", None])
    def test_aggregate_revision_rejects_invalid(self, bad: object) -> None:
        with pytest.raises(InvalidAssetAggregateRevisionError):
            AssetAggregateRevision(bad)  # type: ignore[arg-type]

    def test_business_and_aggregate_revision_are_distinct_types(self) -> None:
        business = AssetRevisionNumber(1)
        aggregate = AssetAggregateRevision(1)
        assert type(business) is not type(aggregate)
        assert business != aggregate  # type: ignore[comparison-overlap]
        assert not isinstance(business, AssetAggregateRevision)
        assert not isinstance(aggregate, AssetRevisionNumber)


class TestResourceTypesAndStates:
    def test_exact_v1_catalog(self) -> None:
        assert ASSET_RESOURCE_TYPES_V1 == frozenset(
            {"asset.image", "asset.document", "asset.audio", "asset.video"}
        )
        assert {m.value for m in AssetResourceType} == ASSET_RESOURCE_TYPES_V1

    @pytest.mark.parametrize(
        "bad",
        ["*", "asset.*", "asset.file", "asset.binary", "asset.other", "asset.pdf", "ASSET.IMAGE"],
    )
    def test_unknown_and_wildcard_resource_types_rejected(self, bad: str) -> None:
        with pytest.raises(InvalidAssetResourceTypeError):
            parse_asset_resource_type(bad)

    def test_lifecycle_quarantine_safety_vocabularies(self) -> None:
        assert {m.value for m in AssetLifecycle} == {
            "active",
            "withdrawn",
            "deleted",
        }
        assert {m.value for m in AssetQuarantineState} == {"clear", "quarantined"}
        assert {m.value for m in AssetRevisionSafetyState} == {
            "pending",
            "passed",
            "failed",
        }
        with pytest.raises(InvalidAssetStateError):
            parse_asset_lifecycle("AVAILABLE")
        with pytest.raises(InvalidAssetStateError):
            parse_asset_quarantine_state("UNKNOWN")
        with pytest.raises(InvalidAssetStateError):
            parse_asset_revision_safety_state("UNSCANNED")


class TestAssetAggregate:
    def test_immutable_and_active_may_have_null_current_revision(self) -> None:
        asset = _asset(current_revision=None, lifecycle=AssetLifecycle.ACTIVE)
        assert asset.current_revision is None
        with pytest.raises(FrozenInstanceError):
            asset.lifecycle = AssetLifecycle.DELETED  # type: ignore[misc]

    def test_withdrawn_and_deleted_may_retain_current_revision(self) -> None:
        rev = AssetRevisionNumber(2)
        withdrawn = _asset(
            lifecycle=AssetLifecycle.WITHDRAWN, current_revision=rev
        )
        deleted = _asset(lifecycle=AssetLifecycle.DELETED, current_revision=rev)
        assert withdrawn.current_revision == rev
        assert deleted.current_revision == rev

    def test_no_storage_url_path_provider_fields(self) -> None:
        names = {f.name for f in fields(Asset)}
        forbidden = {
            "storage_key",
            "url",
            "bucket",
            "path",
            "provider",
            "blob_uri",
            "s3_uri",
            "content_id",
            "usable",
        }
        assert names.isdisjoint(forbidden)

    def test_rejects_non_uuid_tenant(self) -> None:
        with pytest.raises(InvalidAssetIdentityError):
            _asset(tenant_id="not-a-uuid")  # type: ignore[arg-type]


class TestAssetRevision:
    def test_immutable(self) -> None:
        revision = _revision()
        with pytest.raises(FrozenInstanceError):
            revision.byte_size = 9  # type: ignore[misc]

    def test_storage_key_preserved_exactly(self) -> None:
        revision = _revision(storage_key="opaque/key/1")
        assert revision.storage_key == "opaque/key/1"

    def test_storage_key_preserves_surrounding_whitespace(self) -> None:
        keyed = "  opaque/key/1  "
        revision = _revision(storage_key=keyed)
        assert revision.storage_key == "  opaque/key/1  "
        assert revision.storage_key == keyed

    @pytest.mark.parametrize("bad", ["", " ", "   ", "\t"])
    def test_storage_key_rejects_empty_or_whitespace_only(self, bad: str) -> None:
        with pytest.raises(InvalidAssetRevisionError):
            _revision(storage_key=bad)

    def test_storage_key_has_no_path_uri_provider_interpretation(self) -> None:
        source = (
            REPO_ROOT
            / "src"
            / "aieos"
            / "domains"
            / "asset"
            / "domain"
            / "revision.py"
        ).read_text(encoding="utf-8")
        for needle in (
            "pathlib",
            "urlsplit",
            "urlparse",
            "urllib",
            "boto3",
            "s3://",
            "normalize",
            "os.path",
        ):
            assert needle not in source

    @pytest.mark.parametrize("bad", [-1, True, False, 1.5, "0"])
    def test_rejects_invalid_byte_size(self, bad: object) -> None:
        with pytest.raises(InvalidAssetRevisionError):
            _revision(byte_size=bad)

    @pytest.mark.parametrize(
        "bad",
        [
            "abc",
            _SHA256_UPPER,
            " " + _SHA256_OK,
            _SHA256_OK + "a",
            "sha256:" + _SHA256_OK,
            None,
        ],
    )
    def test_rejects_malformed_or_uppercase_sha256(self, bad: object) -> None:
        with pytest.raises(InvalidAssetRevisionError):
            _revision(sha256=bad)

    def test_rejects_naive_datetime(self) -> None:
        with pytest.raises(InvalidAssetRevisionError):
            _revision(created_at=datetime(2026, 1, 1))

    def test_rejects_non_uuid_tenant_and_principal(self) -> None:
        with pytest.raises(InvalidAssetIdentityError):
            _revision(tenant_id=object())  # type: ignore[arg-type]
        with pytest.raises(InvalidAssetIdentityError):
            _revision(created_by_principal_id="x")  # type: ignore[arg-type]

    def test_no_mutable_governance_fields_on_revision(self) -> None:
        names = {f.name for f in fields(AssetRevision)}
        assert names.isdisjoint(
            {
                "safety_state",
                "quarantined",
                "withdrawn",
                "deleted",
                "available",
                "blob_exists",
                "usable",
                "bytes_purged",
            }
        )


class TestAssetRevisionState:
    def test_immutable_and_strict_bool(self) -> None:
        state = _revision_state(bytes_purged=True)
        assert state.bytes_purged is True
        with pytest.raises(FrozenInstanceError):
            state.bytes_purged = False  # type: ignore[misc]
        with pytest.raises(InvalidAssetRevisionStateError):
            _revision_state(bytes_purged=1)  # type: ignore[arg-type]
        with pytest.raises(InvalidAssetRevisionStateError):
            _revision_state(bytes_purged=0)  # type: ignore[arg-type]

    def test_no_physical_authority_fields(self) -> None:
        names = {f.name for f in fields(AssetRevisionState)}
        assert names.isdisjoint({"blob_exists", "available", "usable"})


class TestResourceRefCompatibility:
    @pytest.mark.parametrize("resource_type", list(AssetResourceType))
    def test_each_type_forms_resource_ref(
        self, resource_type: AssetResourceType
    ) -> None:
        asset_id = AssetId.generate()
        revision = AssetRevisionNumber(3)
        ref = ResourceRef(
            resource_type=resource_type.value,
            resource_id=asset_id.value,
            resource_revision=revision.value,
        )
        assert ref.resource_type == resource_type.value
        assert ref.resource_id == asset_id.value
        assert ref.resource_revision == revision.value

    def test_aggregate_revision_is_not_resource_revision_semantics(self) -> None:
        # Documented mapping: ResourceRef.resource_revision <- AssetRevisionNumber
        # AssetAggregateRevision must not be treated as that mapping.
        assert AssetRevisionNumber.__doc__ is not None
        assert "ResourceRef.resource_revision" in AssetRevisionNumber.__doc__
        assert AssetAggregateRevision.__doc__ is not None
        assert "Not a business ResourceRef revision" in AssetAggregateRevision.__doc__
        business = AssetRevisionNumber(5)
        aggregate = AssetAggregateRevision(5)
        ref = ResourceRef(
            resource_type=AssetResourceType.IMAGE.value,
            resource_id=AssetId.generate().value,
            resource_revision=business.value,
        )
        assert ref.resource_revision == business.value
        assert ref.resource_revision == aggregate.value  # equal int, different meaning
        assert type(business) is not type(aggregate)


class TestArchitectureGuards:
    def _py_files(self) -> list[Path]:
        return [p for p in (ASSET_DOMAIN / "domain").rglob("*.py") if p.is_file()]

    def test_no_forbidden_imports(self) -> None:
        hits: list[str] = []
        for path in self._py_files():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                modules: list[str] = []
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    modules = [node.module]
                for name in modules:
                    root = name.split(".")[0]
                    if root in _FORBIDDEN_IMPORT_ROOTS:
                        hits.append(f"{path.name}:{name}")
                    if name.startswith("aieos.domains.content"):
                        hits.append(f"{path.name}:content-coupling:{name}")
        assert hits == []

    def test_no_wildcard_type_matching_in_source(self) -> None:
        for path in self._py_files():
            text = path.read_text(encoding="utf-8")
            assert "fnmatch" not in text
            assert "startswith(\"asset.\")" not in text
            assert "startswith('asset.')" not in text

    def test_no_migration_and_no_composition_change(self) -> None:
        assert not any(p.name.startswith("pedi10b1") for p in MIGRATIONS.glob("*.py"))
        composition = COMPOSITION.read_text(encoding="utf-8")
        assert "domains.asset" not in composition
        assert "AssetId" not in composition
        assert OPENAPI.is_file()

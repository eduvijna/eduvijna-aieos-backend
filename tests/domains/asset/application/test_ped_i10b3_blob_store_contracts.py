"""PED-I10B3 BlobObjectInfo, BlobStore, and StorageKeyFactory contract tests."""

from __future__ import annotations

import inspect
from dataclasses import FrozenInstanceError, fields
from io import BytesIO
from uuid import UUID

import pytest

from aieos.domains.asset.application.blob_store import (
    BlobObjectInfo,
    Uuid7StorageKeyFactory,
)
from aieos.domains.asset.application.errors import (
    BlobAlreadyExistsError,
    InvalidBlobObjectInfoError,
)
from tests.domains.asset.application.fakes import InMemoryBlobStore

pytestmark = pytest.mark.ped_i10b3

_SHA256_OK = "a" * 64
_SHA256_EMPTY = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


class TestBlobObjectInfo:
    def test_valid_metadata_succeeds(self) -> None:
        info = BlobObjectInfo(
            storage_key="opaque-key",
            byte_size=12,
            sha256=_SHA256_OK,
        )
        assert info.storage_key == "opaque-key"
        assert info.byte_size == 12
        assert info.sha256 == _SHA256_OK

    def test_empty_storage_key_rejects(self) -> None:
        with pytest.raises(InvalidBlobObjectInfoError):
            BlobObjectInfo(storage_key="", byte_size=0, sha256=_SHA256_OK)

    def test_whitespace_only_key_rejects(self) -> None:
        with pytest.raises(InvalidBlobObjectInfoError):
            BlobObjectInfo(storage_key="   ", byte_size=0, sha256=_SHA256_OK)
        with pytest.raises(InvalidBlobObjectInfoError):
            BlobObjectInfo(storage_key="\n\t", byte_size=0, sha256=_SHA256_OK)

    def test_accepted_opaque_key_preserved_exactly(self) -> None:
        key = "  MixedCase/Key With Space "
        info = BlobObjectInfo(storage_key=key, byte_size=0, sha256=_SHA256_OK)
        assert info.storage_key == key
        assert info.storage_key != key.strip()
        assert info.storage_key != key.lower()

    def test_byte_size_zero_succeeds(self) -> None:
        info = BlobObjectInfo(storage_key="k", byte_size=0, sha256=_SHA256_OK)
        assert info.byte_size == 0

    def test_negative_size_rejects(self) -> None:
        with pytest.raises(InvalidBlobObjectInfoError):
            BlobObjectInfo(storage_key="k", byte_size=-1, sha256=_SHA256_OK)

    def test_bool_size_rejects(self) -> None:
        with pytest.raises(InvalidBlobObjectInfoError):
            BlobObjectInfo(storage_key="k", byte_size=True, sha256=_SHA256_OK)  # type: ignore[arg-type]
        with pytest.raises(InvalidBlobObjectInfoError):
            BlobObjectInfo(storage_key="k", byte_size=False, sha256=_SHA256_OK)  # type: ignore[arg-type]

    def test_lowercase_64_hex_digest_succeeds(self) -> None:
        info = BlobObjectInfo(storage_key="k", byte_size=1, sha256=_SHA256_OK)
        assert info.sha256 == _SHA256_OK

    def test_uppercase_digest_rejects(self) -> None:
        with pytest.raises(InvalidBlobObjectInfoError):
            BlobObjectInfo(storage_key="k", byte_size=1, sha256="A" * 64)

    def test_malformed_digest_rejects(self) -> None:
        with pytest.raises(InvalidBlobObjectInfoError):
            BlobObjectInfo(storage_key="k", byte_size=1, sha256="z" * 64)
        with pytest.raises(InvalidBlobObjectInfoError):
            BlobObjectInfo(storage_key="k", byte_size=1, sha256="a" * 63)
        with pytest.raises(InvalidBlobObjectInfoError):
            BlobObjectInfo(storage_key="k", byte_size=1, sha256="a" * 65)

    def test_no_url_provider_or_authority_fields(self) -> None:
        names = {f.name for f in fields(BlobObjectInfo)}
        assert names == {"storage_key", "byte_size", "sha256"}
        assert names.isdisjoint(
            {
                "tenant_id",
                "asset_id",
                "asset_revision_id",
                "resource_type",
                "media_type",
                "bucket",
                "container",
                "provider",
                "region",
                "url",
                "signed_url",
                "public_url",
                "filesystem_path",
                "etag",
                "available",
                "usable",
                "safety_state",
                "quarantine_state",
            }
        )

    def test_immutable(self) -> None:
        info = BlobObjectInfo(storage_key="k", byte_size=0, sha256=_SHA256_OK)
        with pytest.raises(FrozenInstanceError):
            info.byte_size = 1  # type: ignore[misc]


class TestBlobStoreContract:
    def test_create_uses_opaque_key_and_returns_actual_metadata(self) -> None:
        store = InMemoryBlobStore()
        payload = b"hello-bytes"
        info = store.create(storage_key="opaque-key", source=BytesIO(payload), byte_size=len(payload))
        assert info.storage_key == "opaque-key"
        assert info.byte_size == len(payload)
        assert info.sha256 == __import__("hashlib").sha256(payload).hexdigest()

    def test_duplicate_create_rejects_and_never_overwrites(self) -> None:
        store = InMemoryBlobStore()
        store.create(storage_key="k", source=BytesIO(b"original"), byte_size=len(b"original"))
        with pytest.raises(BlobAlreadyExistsError):
            store.create(storage_key="k", source=BytesIO(b"replacement"), byte_size=len(b"replacement"))
        assert store.payload("k") == b"original"
        inspected = store.inspect(storage_key="k")
        assert inspected is not None
        assert inspected.byte_size == len(b"original")

    def test_inspect_existing_succeeds_and_absent_returns_none(self) -> None:
        store = InMemoryBlobStore()
        store.create(storage_key="present", source=BytesIO(b"x"), byte_size=len(b"x"))
        found = store.inspect(storage_key="present")
        assert found is not None
        assert found.storage_key == "present"
        assert store.inspect(storage_key="absent") is None

    def test_delete_is_physical_only(self) -> None:
        store = InMemoryBlobStore()
        store.create(storage_key="k", source=BytesIO(b"x"), byte_size=len(b"x"))
        store.delete(storage_key="k")
        assert store.inspect(storage_key="k") is None
        assert store.delete_calls == ["k"]
        store.delete(storage_key="k")
        assert store.inspect(storage_key="k") is None

    def test_empty_payload_is_valid(self) -> None:
        store = InMemoryBlobStore()
        info = store.create(storage_key="empty", source=BytesIO(b""), byte_size=len(b""))
        assert info.byte_size == 0
        assert info.sha256 == _SHA256_EMPTY


class TestStorageKeyFactory:
    def test_produces_nonempty_distinct_values(self) -> None:
        factory = Uuid7StorageKeyFactory()
        keys = {factory.generate() for _ in range(32)}
        assert len(keys) == 32
        for key in keys:
            assert isinstance(key, str)
            assert key
            assert key.strip() == key

    def test_generate_accepts_no_tenant_asset_or_revision_input(self) -> None:
        signature = inspect.signature(Uuid7StorageKeyFactory.generate)
        assert list(signature.parameters) == ["self"]

    def test_no_path_separator_uri_scheme_or_provider_name(self) -> None:
        key = Uuid7StorageKeyFactory().generate()
        assert "/" not in key
        assert "\\" not in key
        assert "://" not in key
        assert not key.startswith("s3:")
        assert not key.startswith("file:")
        assert "minio" not in key.lower()
        assert "azure" not in key.lower()
        assert "gs://" not in key

    def test_uuidv7_hex_generation_on_python_314(self) -> None:
        key = Uuid7StorageKeyFactory().generate()
        parsed = UUID(key)
        assert parsed.version == 7
        assert key == parsed.hex
        assert len(key) == 32
        assert key == key.lower()

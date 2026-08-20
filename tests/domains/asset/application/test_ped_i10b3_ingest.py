"""PED-I10B3 pre-persistence ingest and cross-store non-atomicity tests."""

from __future__ import annotations

import hashlib
import inspect
from io import BytesIO

import pytest

from aieos.domains.asset.application.blob_store import (
    BlobObjectInfo,
    Uuid7StorageKeyFactory,
)
from aieos.domains.asset.application.errors import (
    BlobAlreadyExistsError,
    BlobStoreContractError,
)
from aieos.domains.asset.application.ingest import BlobIngestPreparer, PreparedBlob
from tests.domains.asset.application.fakes import InMemoryBlobStore

pytestmark = pytest.mark.ped_i10b3

_SHA256_OK = "b" * 64


class _FixedKeyFactory:
    def __init__(self, key: str) -> None:
        self._key = key

    def generate(self) -> str:
        return self._key


class _MismatchingBlobStore:
    def __init__(self) -> None:
        self.create_calls = 0
        self.delete_calls = 0

    def create(
        self, *, storage_key: str, source: object, byte_size: int
    ) -> BlobObjectInfo:
        self.create_calls += 1
        payload = source.read()  # type: ignore[union-attr]
        return BlobObjectInfo(
            storage_key=storage_key + "-other",
            byte_size=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
        )

    def inspect(self, *, storage_key: str) -> BlobObjectInfo | None:
        raise AssertionError("ingest must not inspect")

    def delete(self, *, storage_key: str) -> None:
        self.delete_calls += 1


class TestPreparedBlob:
    def test_fields_match_physical_observation_only(self) -> None:
        prepared = PreparedBlob(storage_key="k", byte_size=0, sha256=_SHA256_OK)
        assert prepared.storage_key == "k"
        assert prepared.byte_size == 0
        assert prepared.sha256 == _SHA256_OK


class TestBlobIngestPreparer:
    def test_prepare_generates_key_server_side_and_creates_once(self) -> None:
        store = InMemoryBlobStore()
        factory = Uuid7StorageKeyFactory()
        preparer = BlobIngestPreparer(blob_store=store, storage_key_factory=factory)
        payload = b"ingest-bytes"
        prepared = preparer.prepare(BytesIO(payload), byte_size=len(payload))
        assert store.create_calls == [prepared.storage_key]
        assert len(store.create_calls) == 1
        assert store.delete_calls == []
        assert prepared.byte_size == len(payload)
        assert prepared.sha256 == hashlib.sha256(payload).hexdigest()
        inspected = store.inspect(storage_key=prepared.storage_key)
        assert inspected is not None
        assert inspected.storage_key == prepared.storage_key
        assert inspected.byte_size == prepared.byte_size
        assert inspected.sha256 == prepared.sha256

    def test_prepare_has_no_caller_storage_key_parameter(self) -> None:
        signature = inspect.signature(BlobIngestPreparer.prepare)
        assert list(signature.parameters) == ["self", "source", "byte_size"]
        assert "storage_key" not in signature.parameters
        assert signature.parameters["byte_size"].kind is inspect.Parameter.KEYWORD_ONLY

    def test_returned_key_mismatch_is_contract_error(self) -> None:
        store = _MismatchingBlobStore()
        preparer = BlobIngestPreparer(
            blob_store=store, storage_key_factory=_FixedKeyFactory("generated")
        )
        with pytest.raises(BlobStoreContractError):
            preparer.prepare(BytesIO(b"x"), byte_size=len(b"x"))
        assert store.create_calls == 1
        assert store.delete_calls == 0

    def test_duplicate_key_does_not_overwrite_or_delete(self) -> None:
        store = InMemoryBlobStore()
        store.create(storage_key="fixed-key", source=BytesIO(b"original"), byte_size=len(b"original"))
        preparer = BlobIngestPreparer(
            blob_store=store, storage_key_factory=_FixedKeyFactory("fixed-key")
        )
        with pytest.raises(BlobAlreadyExistsError):
            preparer.prepare(BytesIO(b"replacement"), byte_size=len(b"replacement"))
        assert store.payload("fixed-key") == b"original"
        assert store.delete_calls == []

    def test_no_automatic_compensation_delete_after_successful_prepare(self) -> None:
        store = InMemoryBlobStore()
        preparer = BlobIngestPreparer(
            blob_store=store, storage_key_factory=Uuid7StorageKeyFactory()
        )
        prepared = preparer.prepare(BytesIO(b"keep-me"), byte_size=len(b"keep-me"))
        # Simulated later DB failure / uncertain commit: ingest must not delete.
        assert store.delete_calls == []
        assert store.inspect(storage_key=prepared.storage_key) is not None

    def test_empty_factory_key_is_contract_error_without_delete(self) -> None:
        store = InMemoryBlobStore()
        preparer = BlobIngestPreparer(
            blob_store=store, storage_key_factory=_FixedKeyFactory("   ")
        )
        with pytest.raises(BlobStoreContractError):
            preparer.prepare(BytesIO(b"x"), byte_size=len(b"x"))
        assert store.create_calls == []
        assert store.delete_calls == []

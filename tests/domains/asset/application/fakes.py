"""Test-only in-memory BlobStore / BlobInventory. Not a production adapter."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

from aieos.domains.asset.application.blob_store import BlobObjectInfo, ReadableBinary
from aieos.domains.asset.application.errors import (
    BlobAlreadyExistsError,
    InvalidBlobObjectInfoError,
)


class InMemoryBlobStore:
    """Deterministic fake for PED-I10B3 tests. Must not be composed into runtime."""

    def __init__(self) -> None:
        self._payloads: dict[str, bytes] = {}
        self.create_calls: list[str] = []
        self.inspect_calls: list[str] = []
        self.delete_calls: list[str] = []

    def create(
        self, *, storage_key: str, source: ReadableBinary, byte_size: int
    ) -> BlobObjectInfo:
        self.create_calls.append(storage_key)
        if storage_key in self._payloads:
            raise BlobAlreadyExistsError(
                "physical object already exists for this storage_key"
            )
        if isinstance(byte_size, bool) or not isinstance(byte_size, int) or byte_size < 0:
            raise InvalidBlobObjectInfoError("byte_size must be an integer >= 0")
        chunks: list[bytes] = []
        remaining = byte_size
        while remaining > 0:
            chunk = source.read(min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(bytes(chunk))
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) != byte_size:
            raise InvalidBlobObjectInfoError(
                "declared byte_size does not match bytes read from source"
            )
        self._payloads[storage_key] = payload
        return self._info(storage_key, payload)

    def inspect(self, *, storage_key: str) -> BlobObjectInfo | None:
        self.inspect_calls.append(storage_key)
        payload = self._payloads.get(storage_key)
        if payload is None:
            return None
        return self._info(storage_key, payload)

    def delete(self, *, storage_key: str) -> None:
        self.delete_calls.append(storage_key)
        self._payloads.pop(storage_key, None)

    def iter_objects(self) -> Iterable[BlobObjectInfo]:
        for storage_key, payload in self._payloads.items():
            yield self._info(storage_key, payload)

    def payload(self, storage_key: str) -> bytes | None:
        return self._payloads.get(storage_key)

    @staticmethod
    def _info(storage_key: str, payload: bytes) -> BlobObjectInfo:
        return BlobObjectInfo(
            storage_key=storage_key,
            byte_size=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
        )

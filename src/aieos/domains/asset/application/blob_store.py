"""Provider-neutral BlobStore / BlobInventory ports and physical observations.

storage_key is an opaque server-generated token. It is not Asset identity,
not a ResourceRef, not a URL, and not a filesystem-path contract.
Accepted keys are preserved exactly; they are never stripped, lowercased,
path-split, or parsed as URLs.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from aieos.domains.asset.application.errors import InvalidBlobObjectInfoError

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def require_opaque_storage_key(
    value: object, *, error: type[Exception] = InvalidBlobObjectInfoError
) -> str:
    """Reject empty/whitespace-only keys; preserve accepted keys exactly."""
    if not isinstance(value, str) or not value.strip():
        raise error("storage_key must be a non-empty string")
    return value


def require_byte_size(
    value: object, *, error: type[Exception] = InvalidBlobObjectInfoError
) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise error("byte_size must be an integer >= 0")
    return value


def require_sha256(
    value: object, *, error: type[Exception] = InvalidBlobObjectInfoError
) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise error("sha256 must be exactly 64 lowercase hexadecimal characters")
    return value


@dataclass(frozen=True, slots=True)
class BlobObjectInfo:
    """Observation of one physical object. Not Asset authority."""

    storage_key: str
    byte_size: int
    sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "storage_key", require_opaque_storage_key(self.storage_key)
        )
        object.__setattr__(self, "byte_size", require_byte_size(self.byte_size))
        object.__setattr__(self, "sha256", require_sha256(self.sha256))


class ReadableBinary(Protocol):
    """Streaming binary input. seek()/fileno()/paths are not required."""

    def read(self, size: int = -1) -> bytes: ...


class BlobStore(Protocol):
    """Physical-byte store. PostgreSQL and BlobStore are not one ACID transaction."""

    def create(
        self, *, storage_key: str, source: ReadableBinary
    ) -> BlobObjectInfo:
        """Create a NEW physical object. Existing key => BlobAlreadyExistsError.

        Must not overwrite. Returned storage_key must equal the requested key
        exactly. byte_size/sha256 are observations of bytes actually written.
        """

    def inspect(self, *, storage_key: str) -> BlobObjectInfo | None:
        """Return physical observation, or None only for genuine not-found.

        Infrastructure/permission/network/unknown failure =>
        BlobStoreUnavailableError. Does not infer Asset usability.
        """

    def delete(self, *, storage_key: str) -> None:
        """Physical-only delete. Does not change Asset lifecycle or deletion_evidence.

        B3 ingest MUST NOT call delete as compensation for later DB failure.
        """


class BlobInventory(Protocol):
    """Physical inventory for reconciliation. Ordering has no business meaning."""

    def iter_objects(self) -> Iterable[BlobObjectInfo]: ...


class StorageKeyFactory(Protocol):
    """Server-side opaque storage_key allocator. No tenant/Asset/path inputs."""

    def generate(self) -> str: ...


class Uuid7StorageKeyFactory:
    """Provider-neutral UUIDv7 hex allocator. Syntax is not business identity."""

    def generate(self) -> str:
        return uuid.uuid7().hex

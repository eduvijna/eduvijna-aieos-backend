"""Pre-persistence blob ingest preparation (PED-I10B3).

BlobIngestPreparer writes physical bytes and returns a PreparedBlob observation.
It does not create Asset identity, persist AssetRevision, open PostgreSQL, mark
safety, bind Content, emit events, or call AssetUseAuthority.

Cross-store non-atomicity
-------------------------
PostgreSQL and BlobStore are not one ACID transaction. After prepare() succeeds,
the physical object exists. A later database transaction may succeed, fail
before commit, or have an uncertain outcome.

B3 therefore MUST NOT implement:

    try:
        write_blob()
        commit_database()
    except Exception:
        delete_blob()

as an atomicity substitute. If DB commit is uncertain, automatic physical
deletion can destroy bytes already referenced by a committed AssetRevision.

A successful prepare may leave an orphan candidate. Orphan discovery is
reconciliation work. Reconciliation MUST NOT delete automatically in B3.
Cleanup policy/execution is separately governed later.
"""

from __future__ import annotations

from dataclasses import dataclass

from aieos.domains.asset.application.blob_store import (
    BlobStore,
    ReadableBinary,
    StorageKeyFactory,
    require_byte_size,
    require_opaque_storage_key,
    require_sha256,
)
from aieos.domains.asset.application.errors import BlobStoreContractError


@dataclass(frozen=True, slots=True)
class PreparedBlob:
    """Physical bytes were successfully created and observed.

    Does not mean Asset/AssetRevision committed, current_revision changed,
    safety passed, or Content may bind/publish the object.
    """

    storage_key: str
    byte_size: int
    sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "storage_key", require_opaque_storage_key(self.storage_key)
        )
        object.__setattr__(self, "byte_size", require_byte_size(self.byte_size))
        object.__setattr__(self, "sha256", require_sha256(self.sha256))


class BlobIngestPreparer:
    """Generate an opaque key, create once, return actual physical observations."""

    def __init__(
        self, *, blob_store: BlobStore, storage_key_factory: StorageKeyFactory
    ) -> None:
        self._blob_store = blob_store
        self._storage_key_factory = storage_key_factory

    def prepare(self, source: ReadableBinary) -> PreparedBlob:
        storage_key = self._storage_key_factory.generate()
        if not isinstance(storage_key, str) or not storage_key.strip():
            raise BlobStoreContractError(
                "storage key factory must return a non-empty opaque key"
            )
        observed = self._blob_store.create(storage_key=storage_key, source=source)
        if observed.storage_key != storage_key:
            raise BlobStoreContractError(
                "BlobStore.create returned a storage_key that does not equal "
                "the generated key exactly"
            )
        return PreparedBlob(
            storage_key=observed.storage_key,
            byte_size=observed.byte_size,
            sha256=observed.sha256,
        )

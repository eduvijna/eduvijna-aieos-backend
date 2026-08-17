"""Provider-neutral Asset BlobStore / ingest / reconciliation errors (PED-I10B3).

Adapters translate vendor or filesystem exceptions into this vocabulary.
Provider SDK exception types are not part of this contract.
"""

from __future__ import annotations


class BlobStoreError(Exception):
    """Base error for provider-neutral BlobStore, ingest, and reconciliation failures."""


class BlobStoreUnavailableError(BlobStoreError):
    """Infrastructure, permission, network, or unknown BlobStore failure.

    Distinct from a genuine not-found inspect result (which returns None).
    """


class BlobAlreadyExistsError(BlobStoreError):
    """create() refused because the opaque storage_key already exists.

    Existing bytes must not be overwritten.
    """


class BlobStoreContractError(BlobStoreError):
    """A BlobStore adapter or StorageKeyFactory violated the application contract."""


class InvalidBlobObjectInfoError(BlobStoreError):
    """Physical BlobObjectInfo / PreparedBlob observation is malformed."""


class InvalidBlobReferenceError(BlobStoreError):
    """AuthoritativeBlobReference is malformed."""


class ConflictingBlobReferenceError(BlobStoreError):
    """Multiple authoritative references name one storage_key with conflicting facts."""


class InvalidBlobInventoryError(BlobStoreError):
    """BlobInventory returned conflicting physical metadata for one storage_key."""

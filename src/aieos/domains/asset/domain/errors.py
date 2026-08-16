"""Asset domain semantic errors (ADR-AIEOS-033 / PED-I10B1).

No HTTP status, Problem Details, SQLAlchemy, Alembic, BlobStore, Temporal,
NATS, or vendor SDK exceptions.
"""

from __future__ import annotations


class AssetDomainError(Exception):
    """Base error for Asset domain invariant failures."""


class InvalidAssetIdentityError(AssetDomainError):
    """Raised when an Asset-owned identity is missing, malformed, or not UUIDv7."""


class InvalidAssetRevisionNumberError(AssetDomainError):
    """Raised when AssetRevisionNumber is not a positive integer."""


class InvalidAssetAggregateRevisionError(AssetDomainError):
    """Raised when AssetAggregateRevision is not a non-negative integer."""


class InvalidAssetResourceTypeError(AssetDomainError):
    """Raised when a resource type is outside the exact V1 Asset catalog."""


class InvalidAssetStateError(AssetDomainError):
    """Raised when lifecycle, quarantine, or safety vocabulary is invalid."""


class InvalidAssetError(AssetDomainError):
    """Raised when an Asset aggregate snapshot violates domain invariants."""


class InvalidAssetRevisionError(AssetDomainError):
    """Raised when an AssetRevision violates domain invariants."""


class InvalidAssetRevisionStateError(AssetDomainError):
    """Raised when AssetRevisionState violates domain invariants."""

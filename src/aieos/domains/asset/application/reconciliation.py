"""Deterministic BlobStore / AssetRevision integrity reconciliation (PED-I10B3).

Reads authoritative AssetRevision projections and physical inventory, then
classifies MATCH / MISSING / INTEGRITY_MISMATCH and reports orphan candidates.
Performs ZERO mutation: no delete, no lifecycle/safety change, no
purge-evidence writes, no events, no HTTP.

An orphan candidate is not authorization to delete.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from aieos.domains.asset.application.blob_store import (
    BlobInventory,
    BlobObjectInfo,
    require_byte_size,
    require_opaque_storage_key,
    require_sha256,
)
from aieos.domains.asset.application.errors import (
    ConflictingBlobReferenceError,
    InvalidBlobInventoryError,
    InvalidBlobReferenceError,
)
from aieos.domains.asset.domain.identities import (
    AssetId,
    AssetRevisionId,
    AssetRevisionNumber,
    require_foreign_uuid,
)


@dataclass(frozen=True, slots=True)
class AuthoritativeBlobReference:
    """Minimum authoritative AssetRevision projection for physical reconciliation.

    Not a new domain aggregate. Not current-use / safety / Content authority.
    """

    tenant_id: UUID
    asset_id: AssetId
    asset_revision_id: AssetRevisionId
    revision_number: AssetRevisionNumber
    storage_key: str
    byte_size: int
    sha256: str

    def __post_init__(self) -> None:
        require_foreign_uuid(self.tenant_id, label="tenant_id")
        if not isinstance(self.asset_id, AssetId):
            raise InvalidBlobReferenceError("asset_id must be an AssetId")
        if not isinstance(self.asset_revision_id, AssetRevisionId):
            raise InvalidBlobReferenceError(
                "asset_revision_id must be an AssetRevisionId"
            )
        if not isinstance(self.revision_number, AssetRevisionNumber):
            raise InvalidBlobReferenceError(
                "revision_number must be an AssetRevisionNumber"
            )
        object.__setattr__(
            self,
            "storage_key",
            require_opaque_storage_key(
                self.storage_key, error=InvalidBlobReferenceError
            ),
        )
        object.__setattr__(
            self,
            "byte_size",
            require_byte_size(self.byte_size, error=InvalidBlobReferenceError),
        )
        object.__setattr__(
            self,
            "sha256",
            require_sha256(self.sha256, error=InvalidBlobReferenceError),
        )


class AuthoritativeBlobReferenceSource(Protocol):
    """Port for authoritative AssetRevision blob facts. No PostgreSQL adapter in B3."""

    def iter_references(self) -> Iterable[AuthoritativeBlobReference]: ...


class BlobReferenceStatus(StrEnum):
    """Closed operational reconciliation status. Not AssetUseRejectionReason."""

    MATCH = "MATCH"
    MISSING = "MISSING"
    INTEGRITY_MISMATCH = "INTEGRITY_MISMATCH"


@dataclass(frozen=True, slots=True)
class BlobReferenceCheck:
    """One authoritative reference compared to the physical object at its exact key."""

    reference: AuthoritativeBlobReference
    status: BlobReferenceStatus
    observed: BlobObjectInfo | None


@dataclass(frozen=True, slots=True)
class OrphanBlobCandidate:
    """Physical object whose exact storage_key is not named by any authoritative reference.

    Not authorization to delete. B3 must not delete it automatically.
    """

    object_info: BlobObjectInfo


@dataclass(frozen=True, slots=True)
class BlobReconciliationReport:
    """Immutable reconciliation result. Ordering of inventory has no business meaning."""

    reference_checks: tuple[BlobReferenceCheck, ...]
    orphan_candidates: tuple[OrphanBlobCandidate, ...]


class BlobReconciler:
    """Deterministic, non-mutating integrity reconciliation."""

    def __init__(
        self,
        *,
        inventory: BlobInventory,
        references: AuthoritativeBlobReferenceSource,
    ) -> None:
        self._inventory = inventory
        self._references = references

    def reconcile(self) -> BlobReconciliationReport:
        refs = tuple(self._references.iter_references())
        physical = _index_inventory(self._inventory.iter_objects())
        _reject_conflicting_references(refs)

        checks: list[BlobReferenceCheck] = []
        referenced_keys: set[str] = set()
        for reference in refs:
            referenced_keys.add(reference.storage_key)
            observed = physical.get(reference.storage_key)
            checks.append(
                BlobReferenceCheck(
                    reference=reference,
                    status=_classify(reference, observed),
                    observed=observed,
                )
            )

        orphans = tuple(
            OrphanBlobCandidate(object_info=info)
            for key, info in sorted(physical.items(), key=lambda item: item[0])
            if key not in referenced_keys
        )
        return BlobReconciliationReport(
            reference_checks=tuple(checks),
            orphan_candidates=orphans,
        )


def _index_inventory(objects: Iterable[BlobObjectInfo]) -> dict[str, BlobObjectInfo]:
    indexed: dict[str, BlobObjectInfo] = {}
    for info in objects:
        existing = indexed.get(info.storage_key)
        if existing is None:
            indexed[info.storage_key] = info
            continue
        if (
            existing.byte_size == info.byte_size
            and existing.sha256 == info.sha256
        ):
            continue
        raise InvalidBlobInventoryError(
            "BlobInventory returned conflicting physical metadata for one "
            "storage_key"
        )
    return indexed


def _reject_conflicting_references(
    refs: tuple[AuthoritativeBlobReference, ...],
) -> None:
    expected: dict[str, tuple[int, str]] = {}
    for reference in refs:
        facts = (reference.byte_size, reference.sha256)
        prior = expected.get(reference.storage_key)
        if prior is None:
            expected[reference.storage_key] = facts
        elif prior != facts:
            raise ConflictingBlobReferenceError(
                "authoritative references name the same storage_key with "
                "conflicting byte_size or sha256"
            )


def _classify(
    reference: AuthoritativeBlobReference, observed: BlobObjectInfo | None
) -> BlobReferenceStatus:
    if observed is None:
        return BlobReferenceStatus.MISSING
    if (
        observed.storage_key == reference.storage_key
        and observed.byte_size == reference.byte_size
        and observed.sha256 == reference.sha256
    ):
        return BlobReferenceStatus.MATCH
    return BlobReferenceStatus.INTEGRITY_MISMATCH

"""PED-I10B3 deterministic integrity reconciliation tests."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import FrozenInstanceError, fields
from io import BytesIO
from uuid import uuid7

import pytest

from aieos.domains.asset.application.blob_store import BlobObjectInfo
from aieos.domains.asset.application.errors import (
    ConflictingBlobReferenceError,
    InvalidBlobInventoryError,
    InvalidBlobReferenceError,
)
from aieos.domains.asset.application.reconciliation import (
    AuthoritativeBlobReference,
    BlobReconciler,
    BlobReferenceStatus,
    OrphanBlobCandidate,
)
from aieos.domains.asset.domain.identities import (
    AssetId,
    AssetRevisionId,
    AssetRevisionNumber,
)
from tests.domains.asset.application.fakes import InMemoryBlobStore

pytestmark = pytest.mark.ped_i10b3

_SHA_A = "a" * 64
_SHA_B = "b" * 64


class _ReferenceSource:
    def __init__(self, refs: list[AuthoritativeBlobReference]) -> None:
        self._refs = refs

    def iter_references(self) -> Iterable[AuthoritativeBlobReference]:
        return iter(self._refs)


class _StaticInventory:
    def __init__(self, objects: list[BlobObjectInfo]) -> None:
        self._objects = objects

    def iter_objects(self) -> Iterable[BlobObjectInfo]:
        return iter(self._objects)


def _ref(
    *,
    storage_key: str = "key-1",
    byte_size: int = 4,
    sha256: str = _SHA_A,
    asset_id: AssetId | None = None,
    revision: int = 1,
) -> AuthoritativeBlobReference:
    return AuthoritativeBlobReference(
        tenant_id=uuid7(),
        asset_id=asset_id or AssetId.generate(),
        asset_revision_id=AssetRevisionId.generate(),
        revision_number=AssetRevisionNumber(revision),
        storage_key=storage_key,
        byte_size=byte_size,
        sha256=sha256,
    )


def _info(
    storage_key: str, byte_size: int = 4, sha256: str = _SHA_A
) -> BlobObjectInfo:
    return BlobObjectInfo(
        storage_key=storage_key, byte_size=byte_size, sha256=sha256
    )


class TestAuthoritativeBlobReference:
    def test_exact_fields_and_identity_validation(self) -> None:
        ref = _ref()
        names = {f.name for f in fields(AuthoritativeBlobReference)}
        assert names == {
            "tenant_id",
            "asset_id",
            "asset_revision_id",
            "revision_number",
            "storage_key",
            "byte_size",
            "sha256",
        }
        assert names.isdisjoint(
            {
                "current_revision",
                "lifecycle",
                "quarantine",
                "safety",
                "authorization",
                "provider",
            }
        )
        with pytest.raises(FrozenInstanceError):
            ref.byte_size = 9  # type: ignore[misc]
        with pytest.raises(InvalidBlobReferenceError):
            AuthoritativeBlobReference(
                tenant_id=uuid7(),
                asset_id=AssetId.generate(),
                asset_revision_id=AssetRevisionId.generate(),
                revision_number=AssetRevisionNumber(1),
                storage_key="",
                byte_size=0,
                sha256=_SHA_A,
            )


class TestBlobReconciler:
    def test_referenced_present_exact_is_match(self) -> None:
        store = InMemoryBlobStore()
        store.create(storage_key="k", source=BytesIO(b"abcd"))
        observed = store.inspect(storage_key="k")
        assert observed is not None
        ref = _ref(
            storage_key="k",
            byte_size=observed.byte_size,
            sha256=observed.sha256,
        )
        report = BlobReconciler(
            inventory=store, references=_ReferenceSource([ref])
        ).reconcile()
        assert len(report.reference_checks) == 1
        assert report.reference_checks[0].status == BlobReferenceStatus.MATCH
        assert report.reference_checks[0].observed == observed
        assert report.orphan_candidates == ()

    def test_referenced_absent_is_missing(self) -> None:
        store = InMemoryBlobStore()
        ref = _ref(storage_key="missing")
        report = BlobReconciler(
            inventory=store, references=_ReferenceSource([ref])
        ).reconcile()
        assert report.reference_checks[0].status == BlobReferenceStatus.MISSING
        assert report.reference_checks[0].observed is None
        assert report.orphan_candidates == ()

    def test_size_mismatch_is_integrity_mismatch(self) -> None:
        report = BlobReconciler(
            inventory=_StaticInventory([_info("k", byte_size=9, sha256=_SHA_A)]),
            references=_ReferenceSource(
                [_ref(storage_key="k", byte_size=4, sha256=_SHA_A)]
            ),
        ).reconcile()
        assert report.reference_checks[0].status == (
            BlobReferenceStatus.INTEGRITY_MISMATCH
        )

    def test_digest_mismatch_is_integrity_mismatch(self) -> None:
        report = BlobReconciler(
            inventory=_StaticInventory([_info("k", byte_size=4, sha256=_SHA_B)]),
            references=_ReferenceSource(
                [_ref(storage_key="k", byte_size=4, sha256=_SHA_A)]
            ),
        ).reconcile()
        assert report.reference_checks[0].status == (
            BlobReferenceStatus.INTEGRITY_MISMATCH
        )

    def test_size_and_digest_mismatch_is_integrity_mismatch(self) -> None:
        report = BlobReconciler(
            inventory=_StaticInventory([_info("k", byte_size=9, sha256=_SHA_B)]),
            references=_ReferenceSource(
                [_ref(storage_key="k", byte_size=4, sha256=_SHA_A)]
            ),
        ).reconcile()
        assert report.reference_checks[0].status == (
            BlobReferenceStatus.INTEGRITY_MISMATCH
        )

    def test_inventory_only_key_is_orphan_candidate_and_not_deleted(self) -> None:
        store = InMemoryBlobStore()
        store.create(storage_key="orphan", source=BytesIO(b"xyz"))
        report = BlobReconciler(
            inventory=store, references=_ReferenceSource([])
        ).reconcile()
        assert len(report.orphan_candidates) == 1
        assert isinstance(report.orphan_candidates[0], OrphanBlobCandidate)
        assert report.orphan_candidates[0].object_info.storage_key == "orphan"
        assert store.inspect(storage_key="orphan") is not None
        assert store.delete_calls == []

    def test_every_referenced_key_is_reconciled(self) -> None:
        store = InMemoryBlobStore()
        store.create(storage_key="one", source=BytesIO(b"aa"))
        first = store.inspect(storage_key="one")
        assert first is not None
        refs = [
            _ref(
                storage_key="one",
                byte_size=first.byte_size,
                sha256=first.sha256,
            ),
            _ref(storage_key="two", byte_size=1, sha256=_SHA_A),
        ]
        report = BlobReconciler(
            inventory=store, references=_ReferenceSource(refs)
        ).reconcile()
        assert [c.reference.storage_key for c in report.reference_checks] == [
            "one",
            "two",
        ]
        assert report.reference_checks[0].status == BlobReferenceStatus.MATCH
        assert report.reference_checks[1].status == BlobReferenceStatus.MISSING

    def test_multiple_references_same_key_same_facts_are_accepted(self) -> None:
        observed = _info("shared", byte_size=4, sha256=_SHA_A)
        refs = [
            _ref(storage_key="shared", byte_size=4, sha256=_SHA_A, revision=1),
            _ref(storage_key="shared", byte_size=4, sha256=_SHA_A, revision=2),
        ]
        report = BlobReconciler(
            inventory=_StaticInventory([observed]),
            references=_ReferenceSource(refs),
        ).reconcile()
        assert len(report.reference_checks) == 2
        assert {c.status for c in report.reference_checks} == {
            BlobReferenceStatus.MATCH
        }
        assert report.orphan_candidates == ()

    def test_multiple_references_same_key_conflicting_facts_reject(self) -> None:
        refs = [
            _ref(storage_key="shared", byte_size=4, sha256=_SHA_A),
            _ref(storage_key="shared", byte_size=5, sha256=_SHA_A),
        ]
        with pytest.raises(ConflictingBlobReferenceError):
            BlobReconciler(
                inventory=_StaticInventory([_info("shared")]),
                references=_ReferenceSource(refs),
            ).reconcile()

    def test_conflicting_duplicate_physical_inventory_rejects(self) -> None:
        inventory = _StaticInventory(
            [
                _info("dup", byte_size=4, sha256=_SHA_A),
                _info("dup", byte_size=5, sha256=_SHA_A),
            ]
        )
        with pytest.raises(InvalidBlobInventoryError):
            BlobReconciler(
                inventory=inventory, references=_ReferenceSource([])
            ).reconcile()

    def test_identical_inventory_duplicates_are_deduplicated(self) -> None:
        same = _info("dup", byte_size=4, sha256=_SHA_A)
        report = BlobReconciler(
            inventory=_StaticInventory([same, same]),
            references=_ReferenceSource([]),
        ).reconcile()
        assert len(report.orphan_candidates) == 1
        assert report.orphan_candidates[0].object_info.storage_key == "dup"

    def test_reconcile_performs_zero_mutation(self) -> None:
        store = InMemoryBlobStore()
        store.create(storage_key="keep", source=BytesIO(b"data"))
        BlobReconciler(
            inventory=store, references=_ReferenceSource([])
        ).reconcile()
        assert store.delete_calls == []
        assert store.inspect(storage_key="keep") is not None
        assert store.create_calls == ["keep"]

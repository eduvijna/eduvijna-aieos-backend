"""PED-I10B4 Asset current-use authority behavior tests."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from io import BytesIO
from uuid import UUID, uuid4, uuid7

import pytest

from aieos.domains.asset.application.errors import BlobStoreUnavailableError
from aieos.domains.asset.application.use_authority import (
    AssetCurrentUseAuthority,
    AssetIdentityFacts,
    GoverningSnapshot,
    RevisionFacts,
    RevisionStateFacts,
)
from aieos.domains.asset.domain.state import (
    AssetLifecycle,
    AssetQuarantineState,
    AssetRevisionSafetyState,
)
from aieos.platform.governance.errors import GovernanceUnavailableError
from aieos.platform.resources import ResourceRef
from aieos.platform.resources.asset_use import AssetUseRejectionReason
from tests.domains.asset.application.fakes import InMemoryBlobStore

pytestmark = pytest.mark.ped_i10b4

FIXED = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)
PAYLOAD = b"asset-bytes-v1"
SHA = hashlib.sha256(PAYLOAD).hexdigest()
SIZE = len(PAYLOAD)
KEY = "opaque-storage-token"
TENANT = uuid4()
PRINCIPAL = uuid4()
ASSET_ID = uuid7()
REVISION_ID = uuid7()


def _identity(**overrides: object) -> AssetIdentityFacts:
    values: dict[str, object] = {
        "asset_id": ASSET_ID,
        "resource_type": "asset.image",
        "lifecycle": AssetLifecycle.ACTIVE,
        "quarantine_state": AssetQuarantineState.CLEAR,
        "current_revision": 1,
        "aggregate_revision": 4,
    }
    values.update(overrides)
    return AssetIdentityFacts(**values)  # type: ignore[arg-type]


def _revision(**overrides: object) -> RevisionFacts:
    values: dict[str, object] = {
        "asset_revision_id": REVISION_ID,
        "revision_number": 1,
        "storage_key": KEY,
        "byte_size": SIZE,
        "sha256": SHA,
    }
    values.update(overrides)
    return RevisionFacts(**values)  # type: ignore[arg-type]


def _state(**overrides: object) -> RevisionStateFacts:
    values: dict[str, object] = {
        "safety_state": AssetRevisionSafetyState.PASSED,
        "bytes_purged": False,
    }
    values.update(overrides)
    return RevisionStateFacts(**values)  # type: ignore[arg-type]


def _snap(
    *,
    identity: AssetIdentityFacts | None = None,
    effective: int | None = 1,
    revision: RevisionFacts | None = None,
    state: RevisionStateFacts | None = None,
    missing_identity: bool = False,
) -> GoverningSnapshot:
    if missing_identity:
        return GoverningSnapshot(None, None, None, None)
    ident = identity if identity is not None else _identity()
    rev = revision if revision is not None else _revision()
    st = state if state is not None else _state()
    return GoverningSnapshot(ident, effective, rev, st)


@dataclass
class QueueStore:
    items: list[GoverningSnapshot]
    calls: int = 0
    kwargs: list[dict[str, object]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.items = list(self.items)

    def load_governing_snapshot(
        self,
        *,
        tenant_id: UUID,
        asset_id: UUID,
        resource_type: str,
        pinned_revision: int | None,
    ) -> GoverningSnapshot:
        self.calls += 1
        self.kwargs.append(
            {
                "tenant_id": tenant_id,
                "asset_id": asset_id,
                "resource_type": resource_type,
                "pinned_revision": pinned_revision,
            }
        )
        if not self.items:
            raise AssertionError("unexpected extra governing-state load")
        if len(self.items) == 1:
            return self.items[0]
        return self.items.pop(0)


class ExplodingStore:
    def load_governing_snapshot(self, **kwargs: object) -> GoverningSnapshot:
        raise AssertionError(f"must not probe store: {kwargs}")


class UnavailableBlobStore:
    def __init__(self) -> None:
        self.inspect_calls: list[str] = []

    def inspect(self, *, storage_key: str):
        self.inspect_calls.append(storage_key)
        raise BlobStoreUnavailableError("blob store unavailable")


class RuntimeBugBlobStore:
    def inspect(self, *, storage_key: str):
        raise RuntimeError("SECRET_BLOBSTORE_BUG")


class ChurnStore:
    def __init__(self, template: GoverningSnapshot) -> None:
        self.template = template
        self.n = 0

    def load_governing_snapshot(self, **kwargs: object) -> GoverningSnapshot:
        self.n += 1
        assert self.template.identity is not None
        identity = replace(self.template.identity, aggregate_revision=self.n)
        return replace(self.template, identity=identity)


def _matching_blobs() -> InMemoryBlobStore:
    blobs = InMemoryBlobStore()
    blobs.create(storage_key=KEY, source=BytesIO(PAYLOAD))
    blobs.inspect_calls.clear()
    return blobs


def _authority(
    store: object,
    blobs: object | None = None,
    *,
    attempts: int = 3,
) -> AssetCurrentUseAuthority:
    return AssetCurrentUseAuthority(
        store,  # type: ignore[arg-type]
        blobs if blobs is not None else _matching_blobs(),  # type: ignore[arg-type]
        clock=lambda: FIXED,
        max_positive_attempts=attempts,
    )


def _ref(
    *,
    resource_type: str = "asset.image",
    resource_id: UUID | None = None,
    revision: int | None = None,
) -> ResourceRef:
    return ResourceRef(
        resource_type=resource_type,
        resource_id=resource_id or ASSET_ID,
        resource_revision=revision,
    )


def _assess(authority: AssetCurrentUseAuthority, ref: ResourceRef | None = None):
    return authority.assess_use(
        tenant_id=TENANT, principal_id=PRINCIPAL, resource_ref=ref or _ref()
    )


class TestUsableAndIdentity:
    def test_usable_stable_match(self) -> None:
        blobs = _matching_blobs()
        authority = _authority(QueueStore([_snap()]), blobs)
        result = _assess(authority)
        assert result.usable is True
        assert result.reason_code is None
        assert result.authority_revision == 4
        assert result.observed_at == FIXED
        assert result.observed_at is not None
        assert result.observed_at.tzinfo is not None
        assert blobs.inspect_calls == [KEY]

    def test_unknown_typed_asset_not_found(self) -> None:
        blobs = _matching_blobs()
        authority = _authority(QueueStore([_snap(missing_identity=True)]), blobs)
        result = _assess(authority)
        assert result.usable is False
        assert result.reason_code is AssetUseRejectionReason.NOT_FOUND
        assert result.authority_revision is None
        assert blobs.inspect_calls == []

    def test_unknown_resource_type_does_not_probe(self) -> None:
        authority = _authority(ExplodingStore(), UnavailableBlobStore())
        result = _assess(authority, _ref(resource_type="asset.file"))
        assert result.reason_code is AssetUseRejectionReason.NOT_FOUND
        assert result.authority_revision is None

    def test_resource_type_mismatch_not_found(self) -> None:
        blobs = _matching_blobs()
        authority = _authority(QueueStore([_snap(missing_identity=True)]), blobs)
        result = _assess(authority, _ref(resource_type="asset.document"))
        assert result.reason_code is AssetUseRejectionReason.NOT_FOUND
        assert result.authority_revision is None
        assert blobs.inspect_calls == []


class TestRevisionSelection:
    def test_pinned_revision_is_requested_exactly(self) -> None:
        store = QueueStore([_snap(effective=2, revision=_revision(revision_number=2))])
        _assess(_authority(store), _ref(revision=2))
        assert store.kwargs[0]["pinned_revision"] == 2

    def test_pinned_revision_absent(self) -> None:
        blobs = _matching_blobs()
        snap = GoverningSnapshot(_identity(), 9, None, None)
        authority = _authority(QueueStore([snap]), blobs)
        result = _assess(authority, _ref(revision=9))
        assert result.reason_code is AssetUseRejectionReason.REVISION_NOT_FOUND
        assert result.authority_revision == 4
        assert blobs.inspect_calls == []

    def test_unpinned_current_revision_null(self) -> None:
        blobs = _matching_blobs()
        snap = GoverningSnapshot(
            _identity(current_revision=None), None, None, None
        )
        authority = _authority(QueueStore([snap]), blobs)
        result = _assess(authority, _ref(revision=None))
        assert result.reason_code is AssetUseRejectionReason.REVISION_NOT_FOUND
        assert result.authority_revision == 4
        assert blobs.inspect_calls == []


class TestLifecycleQuarantineSafety:
    def test_deleted(self) -> None:
        result = _assess(
            _authority(
                QueueStore(
                    [_snap(identity=_identity(lifecycle=AssetLifecycle.DELETED))]
                )
            )
        )
        assert result.reason_code is AssetUseRejectionReason.DELETED
        assert result.authority_revision == 4

    def test_withdrawn(self) -> None:
        result = _assess(
            _authority(
                QueueStore(
                    [_snap(identity=_identity(lifecycle=AssetLifecycle.WITHDRAWN))]
                )
            )
        )
        assert result.reason_code is AssetUseRejectionReason.WITHDRAWN

    def test_quarantined(self) -> None:
        result = _assess(
            _authority(
                QueueStore(
                    [
                        _snap(
                            identity=_identity(
                                quarantine_state=AssetQuarantineState.QUARANTINED
                            )
                        )
                    ]
                )
            )
        )
        assert result.reason_code is AssetUseRejectionReason.QUARANTINED

    def test_safety_failed(self) -> None:
        result = _assess(
            _authority(
                QueueStore(
                    [
                        _snap(
                            state=_state(safety_state=AssetRevisionSafetyState.FAILED)
                        )
                    ]
                )
            )
        )
        assert result.reason_code is AssetUseRejectionReason.SAFETY_FAILED

    def test_safety_pending(self) -> None:
        result = _assess(
            _authority(
                QueueStore(
                    [
                        _snap(
                            state=_state(safety_state=AssetRevisionSafetyState.PENDING)
                        )
                    ]
                )
            )
        )
        assert result.reason_code is AssetUseRejectionReason.SAFETY_PENDING

    def test_pinned_historical_plus_current_quarantine(self) -> None:
        result = _assess(
            _authority(
                QueueStore(
                    [
                        _snap(
                            identity=_identity(
                                quarantine_state=AssetQuarantineState.QUARANTINED
                            ),
                            effective=2,
                            revision=_revision(revision_number=2),
                        )
                    ]
                )
            ),
            _ref(revision=2),
        )
        assert result.reason_code is AssetUseRejectionReason.QUARANTINED

    def test_pinned_historical_plus_current_withdrawn(self) -> None:
        result = _assess(
            _authority(
                QueueStore(
                    [
                        _snap(
                            identity=_identity(lifecycle=AssetLifecycle.WITHDRAWN),
                            effective=2,
                            revision=_revision(revision_number=2),
                        )
                    ]
                )
            ),
            _ref(revision=2),
        )
        assert result.reason_code is AssetUseRejectionReason.WITHDRAWN


class TestPrecedence:
    def test_deleted_plus_quarantined(self) -> None:
        result = _assess(
            _authority(
                QueueStore(
                    [
                        _snap(
                            identity=_identity(
                                lifecycle=AssetLifecycle.DELETED,
                                quarantine_state=AssetQuarantineState.QUARANTINED,
                            )
                        )
                    ]
                )
            )
        )
        assert result.reason_code is AssetUseRejectionReason.DELETED

    def test_withdrawn_plus_quarantined(self) -> None:
        result = _assess(
            _authority(
                QueueStore(
                    [
                        _snap(
                            identity=_identity(
                                lifecycle=AssetLifecycle.WITHDRAWN,
                                quarantine_state=AssetQuarantineState.QUARANTINED,
                            )
                        )
                    ]
                )
            )
        )
        assert result.reason_code is AssetUseRejectionReason.WITHDRAWN

    def test_quarantined_plus_safety_failed(self) -> None:
        result = _assess(
            _authority(
                QueueStore(
                    [
                        _snap(
                            identity=_identity(
                                quarantine_state=AssetQuarantineState.QUARANTINED
                            ),
                            state=_state(safety_state=AssetRevisionSafetyState.FAILED),
                        )
                    ]
                )
            )
        )
        assert result.reason_code is AssetUseRejectionReason.QUARANTINED

    def test_safety_failed_plus_bytes_purged(self) -> None:
        blobs = _matching_blobs()
        result = _assess(
            _authority(
                QueueStore(
                    [
                        _snap(
                            state=_state(
                                safety_state=AssetRevisionSafetyState.FAILED,
                                bytes_purged=True,
                            )
                        )
                    ]
                ),
                blobs,
            )
        )
        assert result.reason_code is AssetUseRejectionReason.SAFETY_FAILED
        assert blobs.inspect_calls == []

    def test_safety_pending_plus_bytes_purged(self) -> None:
        blobs = _matching_blobs()
        result = _assess(
            _authority(
                QueueStore(
                    [
                        _snap(
                            state=_state(
                                safety_state=AssetRevisionSafetyState.PENDING,
                                bytes_purged=True,
                            )
                        )
                    ]
                ),
                blobs,
            )
        )
        assert result.reason_code is AssetUseRejectionReason.SAFETY_PENDING
        assert blobs.inspect_calls == []


class TestPhysicalBytes:
    def test_bytes_purged_does_not_inspect(self) -> None:
        blobs = _matching_blobs()
        result = _assess(
            _authority(
                QueueStore([_snap(state=_state(bytes_purged=True))]),
                blobs,
            )
        )
        assert result.reason_code is AssetUseRejectionReason.BYTES_PURGED
        assert result.authority_revision == 4
        assert blobs.inspect_calls == []

    def test_bytes_missing(self) -> None:
        blobs = InMemoryBlobStore()
        result = _assess(_authority(QueueStore([_snap()]), blobs))
        assert result.reason_code is AssetUseRejectionReason.BYTES_MISSING
        assert blobs.inspect_calls == [KEY]

    def test_byte_size_mismatch(self) -> None:
        blobs = _matching_blobs()
        result = _assess(
            _authority(
                QueueStore([_snap(revision=_revision(byte_size=SIZE + 9))]),
                blobs,
            )
        )
        assert result.reason_code is AssetUseRejectionReason.INTEGRITY_MISMATCH

    def test_sha256_mismatch(self) -> None:
        blobs = _matching_blobs()
        result = _assess(
            _authority(
                QueueStore([_snap(revision=_revision(sha256="b" * 64))]),
                blobs,
            )
        )
        assert result.reason_code is AssetUseRejectionReason.INTEGRITY_MISMATCH

    def test_both_integrity_facts_mismatch(self) -> None:
        blobs = _matching_blobs()
        result = _assess(
            _authority(
                QueueStore(
                    [_snap(revision=_revision(byte_size=1, sha256="c" * 64))]
                ),
                blobs,
            )
        )
        assert result.reason_code is AssetUseRejectionReason.INTEGRITY_MISMATCH

    def test_blobstore_unavailable_is_governance_unavailable(self) -> None:
        blobs = UnavailableBlobStore()
        with pytest.raises(GovernanceUnavailableError, match="governance unavailable"):
            _assess(_authority(QueueStore([_snap()]), blobs))
        assert blobs.inspect_calls == [KEY]

    def test_blobstore_runtime_error_is_not_translated(self) -> None:
        with pytest.raises(RuntimeError, match="SECRET_BLOBSTORE_BUG"):
            _assess(_authority(QueueStore([_snap()]), RuntimeBugBlobStore()))

    def test_missing_revision_state_is_governance_unavailable(self) -> None:
        snap = GoverningSnapshot(_identity(), 1, _revision(), None)
        with pytest.raises(GovernanceUnavailableError, match="governance unavailable"):
            _assess(_authority(QueueStore([snap]), _matching_blobs()))


class TestAuthorityMetadataAndCache:
    def test_authority_revision_on_resolved_outcomes(self) -> None:
        result = _assess(
            _authority(
                QueueStore(
                    [_snap(identity=_identity(aggregate_revision=11))]
                )
            )
        )
        assert result.usable is True
        assert result.authority_revision == 11

    def test_not_found_authority_revision_is_none(self) -> None:
        result = _assess(
            _authority(QueueStore([_snap(missing_identity=True)]))
        )
        assert result.authority_revision is None

    def test_observed_at_is_timezone_aware(self) -> None:
        result = _assess(_authority(QueueStore([_snap()])))
        assert result.observed_at is not None
        assert result.observed_at.tzinfo is not None
        assert result.observed_at.utcoffset() is not None

    def test_no_cross_request_positive_cache(self) -> None:
        withdrawn = _snap(identity=_identity(lifecycle=AssetLifecycle.WITHDRAWN))
        store = QueueStore([_snap(), _snap(), withdrawn])
        authority = _authority(store)
        first = _assess(authority)
        assert first.usable is True
        second = _assess(authority)
        assert second.reason_code is AssetUseRejectionReason.WITHDRAWN


class TestCrossStoreStability:
    def test_governance_change_after_inspect_is_not_stale_usable(self) -> None:
        candidate = _snap()
        withdrawn = _snap(identity=_identity(lifecycle=AssetLifecycle.WITHDRAWN))
        store = QueueStore([candidate, withdrawn, withdrawn])
        result = _assess(_authority(store))
        assert result.usable is False
        assert result.reason_code is AssetUseRejectionReason.WITHDRAWN

    def test_unpinned_current_revision_change_does_not_succeed_old_revision(
        self,
    ) -> None:
        key2 = "opaque-storage-token-2"
        rev1 = _snap()
        rev2_pending = _snap(
            identity=_identity(current_revision=2, aggregate_revision=5),
            effective=2,
            revision=_revision(
                asset_revision_id=uuid7(),
                revision_number=2,
                storage_key=key2,
            ),
            state=_state(safety_state=AssetRevisionSafetyState.PENDING),
        )
        blobs = _matching_blobs()
        blobs.create(storage_key=key2, source=BytesIO(PAYLOAD))
        blobs.inspect_calls.clear()
        store = QueueStore([rev1, rev2_pending, rev2_pending])
        result = _assess(_authority(store, blobs), _ref(revision=None))
        assert result.reason_code is AssetUseRejectionReason.SAFETY_PENDING
        assert blobs.inspect_calls == [KEY]

    def test_persistent_churn_fails_closed(self) -> None:
        with pytest.raises(GovernanceUnavailableError, match="governance unavailable"):
            _assess(_authority(ChurnStore(_snap()), _matching_blobs(), attempts=3))

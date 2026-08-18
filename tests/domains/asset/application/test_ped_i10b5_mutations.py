"""PED-I10B5 Asset mutation application semantics (in-memory UoW)."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from io import BytesIO
from uuid import uuid7

import pytest

from aieos.domains.asset.application.blob_store import BlobObjectInfo
from aieos.domains.asset.application.errors import BlobStoreUnavailableError
from aieos.domains.asset.application.ingest import PreparedBlob
from aieos.domains.asset.application.mutation_errors import (
    AssetActivationRejected,
    AssetConflict,
    AssetNotFound,
    AssetTransitionRejected,
)
from aieos.domains.asset.application.mutations import AssetMutationService
from aieos.domains.asset.domain.identities import (
    AssetAggregateRevision,
    AssetId,
    AssetRevisionId,
)
from aieos.domains.asset.domain.resource_type import AssetResourceType
from aieos.domains.asset.domain.revision import AssetRevisionState
from aieos.domains.asset.domain.state import (
    AssetLifecycle,
    AssetQuarantineState,
    AssetRevisionSafetyState,
)
from tests.domains.asset.application.fakes import InMemoryBlobStore
from tests.domains.asset.application.mutation_fakes import (
    AllowAssetMutationAuthorization,
    InMemoryAssetUnitOfWorkFactory,
    asset_audit_kwargs,
)

pytestmark = pytest.mark.ped_i10b5

PAYLOAD = b"asset-bytes-v1"
SHA = hashlib.sha256(PAYLOAD).hexdigest()
SIZE = len(PAYLOAD)
FIXED = datetime(2026, 8, 17, 18, 0, tzinfo=UTC)
ZERO = AssetAggregateRevision(0)


def _clock() -> datetime:
    return FIXED


def _service(
    blobs: InMemoryBlobStore | None = None,
) -> tuple[AssetMutationService, InMemoryAssetUnitOfWorkFactory, InMemoryBlobStore]:
    factory = InMemoryAssetUnitOfWorkFactory()
    store = blobs if blobs is not None else InMemoryBlobStore()
    service = AssetMutationService(
        factory, store, AllowAssetMutationAuthorization(), clock=_clock
    )
    return service, factory, store


def _prepared(blobs: InMemoryBlobStore, payload: bytes = PAYLOAD) -> PreparedBlob:
    key = uuid7().hex
    info = blobs.create(storage_key=key, source=BytesIO(payload))
    return PreparedBlob(
        storage_key=info.storage_key,
        byte_size=info.byte_size,
        sha256=info.sha256,
    )


def _create(service: AssetMutationService, tenant=None, principal=None, asset_id=None):
    tenant = tenant or uuid7()
    principal = principal or uuid7()
    asset_id = asset_id or AssetId.generate()
    asset = service.create_asset(
        tenant_id=tenant,
        principal_id=principal,
        asset_id=asset_id,
        resource_type=AssetResourceType.IMAGE,
        **asset_audit_kwargs(principal),
    )
    return asset, tenant, principal


def _register(service, blobs, tenant, principal, asset_id):
    prepared = _prepared(blobs)
    revision_id = AssetRevisionId.generate()
    registered = service.register_revision(
        tenant_id=tenant,
        principal_id=principal,
        asset_id=asset_id,
        asset_revision_id=revision_id,
        prepared=prepared,
        media_type="image/png",
        **asset_audit_kwargs(principal),
    )
    return registered, prepared


class InspectProbe:
    def __init__(self, inner: InMemoryBlobStore, on_inspect=None) -> None:
        self.inner = inner
        self.calls: list[str] = []
        self.on_inspect = on_inspect

    def inspect(self, *, storage_key: str) -> BlobObjectInfo | None:
        self.calls.append(storage_key)
        if self.on_inspect is not None:
            self.on_inspect()
        return self.inner.inspect(storage_key=storage_key)

    def create(self, *, storage_key: str, source: object) -> BlobObjectInfo:
        raise AssertionError("create must not be called")

    def delete(self, *, storage_key: str) -> None:
        raise AssertionError("delete must not be called")


def _activatable_at_five(service: AssetMutationService, blobs: InMemoryBlobStore):
    asset, tenant, principal = _create(service)
    target, _ = _register(service, blobs, tenant, principal, asset.asset_id)
    historical, _ = _register(service, blobs, tenant, principal, asset.asset_id)
    service.mark_safety_passed(
        tenant_id=tenant,
        principal_id=principal,
        asset_id=asset.asset_id,
        asset_revision_id=target.revision.asset_revision_id,
        expected_aggregate_revision=ZERO,
            **asset_audit_kwargs(principal),
        )
    service.withdraw_asset(
        tenant_id=tenant,
        principal_id=principal,
        asset_id=asset.asset_id,
        expected_aggregate_revision=AssetAggregateRevision(1),
            **asset_audit_kwargs(principal),
        )
    service.restore_asset(
        tenant_id=tenant,
        principal_id=principal,
        asset_id=asset.asset_id,
        expected_aggregate_revision=AssetAggregateRevision(2),
            **asset_audit_kwargs(principal),
        )
    service.quarantine_asset(
        tenant_id=tenant,
        principal_id=principal,
        asset_id=asset.asset_id,
        expected_aggregate_revision=AssetAggregateRevision(3),
            **asset_audit_kwargs(principal),
        )
    head = service.clear_quarantine(
        tenant_id=tenant,
        principal_id=principal,
        asset_id=asset.asset_id,
        expected_aggregate_revision=AssetAggregateRevision(4),
            **asset_audit_kwargs(principal),
        )
    assert int(head.aggregate_revision) == 5
    assert head.current_revision is None
    return tenant, principal, asset.asset_id, target, historical, head


class UnavailableBlobStore:
    def inspect(self, *, storage_key: str) -> BlobObjectInfo | None:
        raise BlobStoreUnavailableError("blob store unavailable")

    def create(self, *, storage_key: str, source: object) -> BlobObjectInfo:
        raise AssertionError("create must not be called")

    def delete(self, *, storage_key: str) -> None:
        raise AssertionError("delete must not be called")


class TestCreate:
    def test_starts_active_clear_null_revision_aggregate_zero(self) -> None:
        service, _, _ = _service()
        asset, _, principal = _create(service)
        assert asset.lifecycle is AssetLifecycle.ACTIVE
        assert asset.quarantine_state is AssetQuarantineState.CLEAR
        assert asset.current_revision is None
        assert int(asset.aggregate_revision) == 0
        assert asset.resource_type is AssetResourceType.IMAGE
        assert asset.created_at == FIXED
        assert asset.created_at.tzinfo is not None
        assert asset.created_at.utcoffset() is not None
        assert asset.created_by_principal_id == principal

    def test_replay_compatible_even_after_later_mutation(self) -> None:
        service, _, _ = _service()
        first, tenant, principal = _create(service)
        withdrawn = service.withdraw_asset(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=first.asset_id,
            expected_aggregate_revision=ZERO,
            **asset_audit_kwargs(principal),
        )
        assert withdrawn.lifecycle is AssetLifecycle.WITHDRAWN
        replayed = service.create_asset(
            tenant_id=tenant,
            principal_id=uuid7(),
            asset_id=first.asset_id,
            resource_type=AssetResourceType.IMAGE,
            **asset_audit_kwargs(principal),
        )
        assert replayed.asset_id == first.asset_id
        assert replayed.lifecycle is AssetLifecycle.WITHDRAWN
        assert int(replayed.aggregate_revision) == 1

    def test_conflicting_resource_type_replay_rejected(self) -> None:
        service, _, _ = _service()
        first, tenant, principal = _create(service)
        with pytest.raises(AssetConflict):
            service.create_asset(
                tenant_id=tenant,
                principal_id=principal,
                asset_id=first.asset_id,
                resource_type=AssetResourceType.DOCUMENT,
            **asset_audit_kwargs(principal),
        )

    def test_cross_tenant_same_id_is_conflict_not_a_probe(self) -> None:
        service, factory, _ = _service()
        first, _, principal = _create(service)
        other = uuid7()
        with pytest.raises(AssetConflict):
            service.create_asset(
                tenant_id=other,
                principal_id=principal,
                asset_id=first.asset_id,
                resource_type=AssetResourceType.IMAGE,
            **asset_audit_kwargs(principal),
        )
        with factory(other) as uow:
            assert uow.assets.get(first.asset_id) is None


class TestRevisionRegistration:
    def test_first_revision_is_one_pending_not_activated(self) -> None:
        service, factory, blobs = _service()
        asset, tenant, principal = _create(service)
        registered, _ = _register(service, blobs, tenant, principal, asset.asset_id)
        assert int(registered.revision.revision_number) == 1
        assert registered.state.safety_state is AssetRevisionSafetyState.PENDING
        assert registered.state.bytes_purged is False
        with factory(tenant) as uow:
            head = uow.assets.get(asset.asset_id)
            assert head is not None
            assert head.current_revision is None
            assert int(head.aggregate_revision) == 0
            assert head.lifecycle is AssetLifecycle.ACTIVE
        assert blobs.delete_calls == []

    def test_later_revisions_increment_monotonically(self) -> None:
        service, _, blobs = _service()
        asset, tenant, principal = _create(service)
        first, _ = _register(service, blobs, tenant, principal, asset.asset_id)
        second, _ = _register(service, blobs, tenant, principal, asset.asset_id)
        assert int(first.revision.revision_number) == 1
        assert int(second.revision.revision_number) == 2

    def test_withdrawn_registration_succeeds_deleted_fails(self) -> None:
        service, _, blobs = _service()
        asset, tenant, principal = _create(service)
        service.withdraw_asset(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset.asset_id,
            expected_aggregate_revision=ZERO,
            **asset_audit_kwargs(principal),
        )
        registered, _ = _register(service, blobs, tenant, principal, asset.asset_id)
        assert int(registered.revision.revision_number) == 1
        deleted = service.delete_asset(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset.asset_id,
            expected_aggregate_revision=AssetAggregateRevision(1),
            **asset_audit_kwargs(principal),
        )
        assert deleted.current_revision is None
        with pytest.raises(AssetTransitionRejected):
            _register(service, blobs, tenant, principal, asset.asset_id)
        assert blobs.delete_calls == []

    def test_revision_id_replay_and_conflict(self) -> None:
        service, _, blobs = _service()
        asset, tenant, principal = _create(service)
        prepared = _prepared(blobs)
        revision_id = AssetRevisionId.generate()
        first = service.register_revision(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset.asset_id,
            asset_revision_id=revision_id,
            prepared=prepared,
            media_type="image/png",
            **asset_audit_kwargs(principal),
        )
        replayed = service.register_revision(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset.asset_id,
            asset_revision_id=revision_id,
            prepared=prepared,
            media_type="image/png",
            **asset_audit_kwargs(principal),
        )
        assert replayed.revision.asset_revision_id == first.revision.asset_revision_id
        assert int(replayed.revision.revision_number) == 1
        other = PreparedBlob(
            storage_key=prepared.storage_key,
            byte_size=prepared.byte_size,
            sha256="a" * 64,
        )
        with pytest.raises(AssetConflict):
            service.register_revision(
                tenant_id=tenant,
                principal_id=principal,
                asset_id=asset.asset_id,
                asset_revision_id=revision_id,
                prepared=other,
                media_type="image/png",
            **asset_audit_kwargs(principal),
        )


class TestActivation:
    def _passed(self, service, blobs, tenant, principal, asset_id):
        registered, prepared = _register(service, blobs, tenant, principal, asset_id)
        asset, _ = service.mark_safety_passed(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset_id,
            asset_revision_id=registered.revision.asset_revision_id,
            expected_aggregate_revision=ZERO,
            **asset_audit_kwargs(principal),
        )
        return registered, prepared, asset

    def test_passed_valid_bytes_activates_and_increments_once(self) -> None:
        service, _, blobs = _service()
        asset, tenant, principal = _create(service)
        registered, _, head = self._passed(
            service, blobs, tenant, principal, asset.asset_id
        )
        activated = service.activate_revision(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset.asset_id,
            resource_type=AssetResourceType.IMAGE,
            revision_number=registered.revision.revision_number,
            expected_aggregate_revision=head.aggregate_revision,
            **asset_audit_kwargs(principal),
        )
        assert activated.current_revision == registered.revision.revision_number
        assert int(activated.aggregate_revision) == int(head.aggregate_revision) + 1
        assert blobs.delete_calls == []

    def test_pending_and_failed_reject(self) -> None:
        service, _, blobs = _service()
        asset, tenant, principal = _create(service)
        registered, _ = _register(service, blobs, tenant, principal, asset.asset_id)
        with pytest.raises(AssetActivationRejected) as pending:
            service.activate_revision(
                tenant_id=tenant,
                principal_id=principal,
                asset_id=asset.asset_id,
                resource_type=AssetResourceType.IMAGE,
                revision_number=registered.revision.revision_number,
                expected_aggregate_revision=ZERO,
            **asset_audit_kwargs(principal),
        )
        assert pending.value.reason == "safety_pending"
        service.mark_safety_failed(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset.asset_id,
            asset_revision_id=registered.revision.asset_revision_id,
            expected_aggregate_revision=ZERO,
            **asset_audit_kwargs(principal),
        )
        with pytest.raises(AssetActivationRejected) as failed:
            service.activate_revision(
                tenant_id=tenant,
                principal_id=principal,
                asset_id=asset.asset_id,
                resource_type=AssetResourceType.IMAGE,
                revision_number=registered.revision.revision_number,
                expected_aggregate_revision=AssetAggregateRevision(1),
            **asset_audit_kwargs(principal),
        )
        assert failed.value.reason == "safety_failed"

    def test_missing_size_and_sha_mismatch_reject(self) -> None:
        service, _, blobs = _service()
        asset, tenant, principal = _create(service)
        registered, prepared, head = self._passed(
            service, blobs, tenant, principal, asset.asset_id
        )
        blobs._payloads.pop(prepared.storage_key)
        with pytest.raises(AssetActivationRejected) as missing:
            service.activate_revision(
                tenant_id=tenant,
                principal_id=principal,
                asset_id=asset.asset_id,
                resource_type=AssetResourceType.IMAGE,
                revision_number=registered.revision.revision_number,
                expected_aggregate_revision=head.aggregate_revision,
            **asset_audit_kwargs(principal),
        )
        assert missing.value.reason == "bytes_missing"
        blobs.create(storage_key=prepared.storage_key, source=BytesIO(b"xx"))
        with pytest.raises(AssetActivationRejected) as size:
            service.activate_revision(
                tenant_id=tenant,
                principal_id=principal,
                asset_id=asset.asset_id,
                resource_type=AssetResourceType.IMAGE,
                revision_number=registered.revision.revision_number,
                expected_aggregate_revision=head.aggregate_revision,
            **asset_audit_kwargs(principal),
        )
        assert size.value.reason == "integrity_mismatch"
        same_len = b"Q" * SIZE
        blobs._payloads[prepared.storage_key] = same_len
        with pytest.raises(AssetActivationRejected) as sha:
            service.activate_revision(
                tenant_id=tenant,
                principal_id=principal,
                asset_id=asset.asset_id,
                resource_type=AssetResourceType.IMAGE,
                revision_number=registered.revision.revision_number,
                expected_aggregate_revision=head.aggregate_revision,
            **asset_audit_kwargs(principal),
        )
        assert sha.value.reason == "integrity_mismatch"

    def test_unavailable_and_stale_expected_and_purged(self) -> None:
        factory = InMemoryAssetUnitOfWorkFactory()
        blobs = InMemoryBlobStore()
        service = AssetMutationService(
            factory, UnavailableBlobStore(), AllowAssetMutationAuthorization(), clock=_clock
        )
        asset, tenant, principal = _create(service)
        registered, _ = _register(
            AssetMutationService(factory, blobs, AllowAssetMutationAuthorization(), clock=_clock),
            blobs,
            tenant,
            principal,
            asset.asset_id,
        )
        AssetMutationService(factory, blobs, AllowAssetMutationAuthorization(), clock=_clock).mark_safety_passed(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset.asset_id,
            asset_revision_id=registered.revision.asset_revision_id,
            expected_aggregate_revision=ZERO,
            **asset_audit_kwargs(principal),
        )
        with pytest.raises(BlobStoreUnavailableError):
            service.activate_revision(
                tenant_id=tenant,
                principal_id=principal,
                asset_id=asset.asset_id,
                resource_type=AssetResourceType.IMAGE,
                revision_number=registered.revision.revision_number,
                expected_aggregate_revision=AssetAggregateRevision(1),
            **asset_audit_kwargs(principal),
        )
        usable = AssetMutationService(factory, blobs, AllowAssetMutationAuthorization(), clock=_clock)
        with pytest.raises(AssetConflict):
            usable.activate_revision(
                tenant_id=tenant,
                principal_id=principal,
                asset_id=asset.asset_id,
                resource_type=AssetResourceType.IMAGE,
                revision_number=registered.revision.revision_number,
                expected_aggregate_revision=ZERO,
            **asset_audit_kwargs(principal),
        )
        state = factory.catalog.states[registered.revision.asset_revision_id.value]
        factory.catalog.states[registered.revision.asset_revision_id.value] = (
            AssetRevisionState(
                tenant_id=state.tenant_id,
                asset_id=state.asset_id,
                asset_revision_id=state.asset_revision_id,
                revision_number=state.revision_number,
                safety_state=state.safety_state,
                bytes_purged=True,
                updated_at=state.updated_at,
            )
        )
        with pytest.raises(AssetActivationRejected) as purged:
            usable.activate_revision(
                tenant_id=tenant,
                principal_id=principal,
                asset_id=asset.asset_id,
                resource_type=AssetResourceType.IMAGE,
                revision_number=registered.revision.revision_number,
                expected_aggregate_revision=AssetAggregateRevision(1),
            **asset_audit_kwargs(principal),
        )
        assert purged.value.reason == "bytes_purged"

    def test_future_expected_revision_conflicts_without_inspect(self) -> None:
        factory = InMemoryAssetUnitOfWorkFactory()
        blobs = InMemoryBlobStore()
        writer = AssetMutationService(factory, blobs, AllowAssetMutationAuthorization(), clock=_clock)
        tenant, principal, asset_id, target, _, head = _activatable_at_five(
            writer, blobs
        )
        probe = InspectProbe(blobs)
        service = AssetMutationService(factory, probe, AllowAssetMutationAuthorization(), clock=_clock)
        with pytest.raises(AssetConflict):
            service.activate_revision(
                tenant_id=tenant,
                principal_id=principal,
                asset_id=asset_id,
                resource_type=AssetResourceType.IMAGE,
                revision_number=target.revision.revision_number,
                expected_aggregate_revision=AssetAggregateRevision(6),
            **asset_audit_kwargs(principal),
        )
        assert probe.calls == []
        with factory(tenant) as uow:
            loaded = uow.assets.get(asset_id)
        assert loaded is not None
        assert loaded.current_revision is None
        assert int(loaded.aggregate_revision) == 5
        assert int(head.aggregate_revision) == 5

    def test_future_expected_does_not_inspect_so_concurrent_bump_cannot_succeed(
        self,
    ) -> None:
        factory = InMemoryAssetUnitOfWorkFactory()
        blobs = InMemoryBlobStore()
        writer = AssetMutationService(factory, blobs, AllowAssetMutationAuthorization(), clock=_clock)
        tenant, principal, asset_id, target, historical, _ = _activatable_at_five(
            writer, blobs
        )

        def bump_historical() -> None:
            writer.mark_safety_failed(
                tenant_id=tenant,
                principal_id=principal,
                asset_id=asset_id,
                asset_revision_id=historical.revision.asset_revision_id,
                expected_aggregate_revision=AssetAggregateRevision(5),
            **asset_audit_kwargs(principal),
        )

        probe = InspectProbe(blobs, on_inspect=bump_historical)
        service = AssetMutationService(factory, probe, AllowAssetMutationAuthorization(), clock=_clock)
        with pytest.raises(AssetConflict):
            service.activate_revision(
                tenant_id=tenant,
                principal_id=principal,
                asset_id=asset_id,
                resource_type=AssetResourceType.IMAGE,
                revision_number=target.revision.revision_number,
                expected_aggregate_revision=AssetAggregateRevision(6),
            **asset_audit_kwargs(principal),
        )
        assert probe.calls == []
        with factory(tenant) as uow:
            loaded = uow.assets.get(asset_id)
            state = uow.revision_states.get(historical.revision.asset_revision_id)
        assert loaded is not None
        assert loaded.current_revision is None
        assert int(loaded.aggregate_revision) == 5
        assert state is not None
        assert state.safety_state is AssetRevisionSafetyState.PENDING

    def test_aggregate_change_during_inspect_conflicts(self) -> None:
        factory = InMemoryAssetUnitOfWorkFactory()
        blobs = InMemoryBlobStore()
        writer = AssetMutationService(factory, blobs, AllowAssetMutationAuthorization(), clock=_clock)
        tenant, principal, asset_id, target, historical, head = _activatable_at_five(
            writer, blobs
        )

        def bump_historical() -> None:
            writer.mark_safety_failed(
                tenant_id=tenant,
                principal_id=principal,
                asset_id=asset_id,
                asset_revision_id=historical.revision.asset_revision_id,
                expected_aggregate_revision=head.aggregate_revision,
            **asset_audit_kwargs(principal),
        )

        probe = InspectProbe(blobs, on_inspect=bump_historical)
        service = AssetMutationService(factory, probe, AllowAssetMutationAuthorization(), clock=_clock)
        with pytest.raises(AssetConflict):
            service.activate_revision(
                tenant_id=tenant,
                principal_id=principal,
                asset_id=asset_id,
                resource_type=AssetResourceType.IMAGE,
                revision_number=target.revision.revision_number,
                expected_aggregate_revision=head.aggregate_revision,
            **asset_audit_kwargs(principal),
        )
        assert probe.calls == [target.revision.storage_key]
        with factory(tenant) as uow:
            loaded = uow.assets.get(asset_id)
        assert loaded is not None
        assert loaded.current_revision is None
        assert int(loaded.aggregate_revision) == 6


class TestLifecycleQuarantineSafety:
    def test_lifecycle_transitions_and_terminal_delete(self) -> None:
        service, _, blobs = _service()
        asset, tenant, principal = _create(service)
        withdrawn = service.withdraw_asset(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset.asset_id,
            expected_aggregate_revision=ZERO,
            **asset_audit_kwargs(principal),
        )
        assert withdrawn.lifecycle is AssetLifecycle.WITHDRAWN
        assert int(withdrawn.aggregate_revision) == 1
        restored = service.restore_asset(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset.asset_id,
            expected_aggregate_revision=AssetAggregateRevision(1),
            **asset_audit_kwargs(principal),
        )
        assert restored.lifecycle is AssetLifecycle.ACTIVE
        deleted = service.delete_asset(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset.asset_id,
            expected_aggregate_revision=AssetAggregateRevision(2),
            **asset_audit_kwargs(principal),
        )
        assert deleted.lifecycle is AssetLifecycle.DELETED
        assert deleted.current_revision is None
        with pytest.raises(AssetTransitionRejected):
            service.restore_asset(
                tenant_id=tenant,
                principal_id=principal,
                asset_id=asset.asset_id,
                expected_aggregate_revision=AssetAggregateRevision(3),
            **asset_audit_kwargs(principal),
        )
        with pytest.raises(AssetTransitionRejected):
            service.withdraw_asset(
                tenant_id=tenant,
                principal_id=principal,
                asset_id=asset.asset_id,
                expected_aggregate_revision=AssetAggregateRevision(3),
            **asset_audit_kwargs(principal),
        )
        with pytest.raises(AssetTransitionRejected):
            service.delete_asset(
                tenant_id=tenant,
                principal_id=principal,
                asset_id=asset.asset_id,
                expected_aggregate_revision=AssetAggregateRevision(3),
            **asset_audit_kwargs(principal),
        )
        assert blobs.delete_calls == []

    def test_stale_expected_revision_and_retained_current_revision(self) -> None:
        service, _, blobs = _service()
        asset, tenant, principal = _create(service)
        registered, _ = _register(service, blobs, tenant, principal, asset.asset_id)
        passed, _ = service.mark_safety_passed(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset.asset_id,
            asset_revision_id=registered.revision.asset_revision_id,
            expected_aggregate_revision=ZERO,
            **asset_audit_kwargs(principal),
        )
        activated = service.activate_revision(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset.asset_id,
            resource_type=AssetResourceType.IMAGE,
            revision_number=registered.revision.revision_number,
            expected_aggregate_revision=passed.aggregate_revision,
            **asset_audit_kwargs(principal),
        )
        with pytest.raises(AssetConflict):
            service.withdraw_asset(
                tenant_id=tenant,
                principal_id=principal,
                asset_id=asset.asset_id,
                expected_aggregate_revision=ZERO,
            **asset_audit_kwargs(principal),
        )
        withdrawn = service.withdraw_asset(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset.asset_id,
            expected_aggregate_revision=activated.aggregate_revision,
            **asset_audit_kwargs(principal),
        )
        assert withdrawn.current_revision == registered.revision.revision_number
        deleted = service.delete_asset(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset.asset_id,
            expected_aggregate_revision=withdrawn.aggregate_revision,
            **asset_audit_kwargs(principal),
        )
        assert deleted.current_revision == registered.revision.revision_number
        assert blobs.delete_calls == []

    def test_quarantine_round_trip_and_deleted_reject(self) -> None:
        service, _, _ = _service()
        asset, tenant, principal = _create(service)
        quarantined = service.quarantine_asset(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset.asset_id,
            expected_aggregate_revision=ZERO,
            **asset_audit_kwargs(principal),
        )
        assert quarantined.quarantine_state is AssetQuarantineState.QUARANTINED
        assert quarantined.lifecycle is AssetLifecycle.ACTIVE
        assert quarantined.current_revision is None
        assert int(quarantined.aggregate_revision) == 1
        cleared = service.clear_quarantine(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset.asset_id,
            expected_aggregate_revision=AssetAggregateRevision(1),
            **asset_audit_kwargs(principal),
        )
        assert cleared.quarantine_state is AssetQuarantineState.CLEAR
        deleted = service.delete_asset(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset.asset_id,
            expected_aggregate_revision=cleared.aggregate_revision,
            **asset_audit_kwargs(principal),
        )
        with pytest.raises(AssetTransitionRejected):
            service.quarantine_asset(
                tenant_id=tenant,
                principal_id=principal,
                asset_id=deleted.asset_id,
                expected_aggregate_revision=deleted.aggregate_revision,
            **asset_audit_kwargs(principal),
        )

    def test_safety_transitions_historical_and_after_delete(self) -> None:
        service, factory, blobs = _service()
        asset, tenant, principal = _create(service)
        first, _ = _register(service, blobs, tenant, principal, asset.asset_id)
        second, _ = _register(service, blobs, tenant, principal, asset.asset_id)
        passed, state = service.mark_safety_passed(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset.asset_id,
            asset_revision_id=first.revision.asset_revision_id,
            expected_aggregate_revision=ZERO,
            **asset_audit_kwargs(principal),
        )
        assert state.safety_state is AssetRevisionSafetyState.PASSED
        assert int(passed.aggregate_revision) == 1
        failed, failed_state = service.mark_safety_failed(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset.asset_id,
            asset_revision_id=first.revision.asset_revision_id,
            expected_aggregate_revision=AssetAggregateRevision(1),
            **asset_audit_kwargs(principal),
        )
        assert failed_state.safety_state is AssetRevisionSafetyState.FAILED
        assert int(failed.aggregate_revision) == 2
        with pytest.raises(AssetTransitionRejected):
            service.mark_safety_passed(
                tenant_id=tenant,
                principal_id=principal,
                asset_id=asset.asset_id,
                asset_revision_id=first.revision.asset_revision_id,
                expected_aggregate_revision=AssetAggregateRevision(2),
            **asset_audit_kwargs(principal),
        )
        deleted = service.delete_asset(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset.asset_id,
            expected_aggregate_revision=AssetAggregateRevision(2),
            **asset_audit_kwargs(principal),
        )
        finalized, terminal = service.mark_safety_failed(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset.asset_id,
            asset_revision_id=second.revision.asset_revision_id,
            expected_aggregate_revision=deleted.aggregate_revision,
            **asset_audit_kwargs(principal),
        )
        assert terminal.safety_state is AssetRevisionSafetyState.FAILED
        assert finalized.lifecycle is AssetLifecycle.DELETED
        with factory(tenant) as uow:
            revision = uow.revisions.get(first.revision.asset_revision_id)
            assert revision is not None
            assert revision.sha256 == first.revision.sha256
            assert revision.byte_size == first.revision.byte_size
            assert revision.storage_key == first.revision.storage_key

    def test_unknown_asset_is_not_found(self) -> None:
        service, _, _ = _service()
        tenant, principal = uuid7(), uuid7()
        with pytest.raises(AssetNotFound):
            service.withdraw_asset(
                tenant_id=tenant,
                principal_id=principal,
                asset_id=AssetId.generate(),
                expected_aggregate_revision=ZERO,
                **asset_audit_kwargs(principal),
            )

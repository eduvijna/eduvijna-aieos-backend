"""PED-I10B5 PostgreSQL write UoW, RLS, concurrency, and activation tests."""

from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from io import BytesIO
from uuid import UUID, uuid7

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.domains.asset.application.errors import BlobStoreUnavailableError
from aieos.domains.asset.application.ingest import PreparedBlob
from aieos.domains.asset.application.mutation_errors import (
    AssetActivationRejected,
    AssetConflict,
    AssetNotFound,
    AssetPersistenceFailed,
    AssetTransitionRejected,
)
from aieos.domains.asset.application.mutations import AssetMutationService
from aieos.domains.asset.domain.identities import (
    AssetAggregateRevision,
    AssetId,
    AssetRevisionId,
)
from aieos.domains.asset.domain.resource_type import AssetResourceType
from aieos.domains.asset.domain.state import (
    AssetLifecycle,
    AssetQuarantineState,
    AssetRevisionSafetyState,
)
from aieos.domains.asset.infrastructure.persistence.postgres_use_authority import (
    PostgresAssetUseAuthority,
)
from aieos.domains.asset.infrastructure.persistence.uow import (
    SqlAlchemyAssetUnitOfWorkFactory,
)
from aieos.domains.asset.infrastructure.persistence.write_repositories import (
    SqlAlchemyAssetRevisionStateWriteRepository,
    SqlAlchemyAssetWriteRepository,
)
from aieos.platform.resources import ResourceRef
from aieos.platform.resources.asset_use import AssetUseRejectionReason
from tests.domains.asset.application.fakes import InMemoryBlobStore

pytestmark = pytest.mark.ped_i10b5

PAYLOAD = b"asset-bytes-v1"
SHA = hashlib.sha256(PAYLOAD).hexdigest()
SIZE = len(PAYLOAD)
FIXED = datetime(2026, 8, 17, 18, 0, tzinfo=UTC)
ZERO = AssetAggregateRevision(0)


def _clock() -> datetime:
    return FIXED


def _service(
    runtime_engine: Engine, blobs: InMemoryBlobStore | None = None
) -> tuple[AssetMutationService, InMemoryBlobStore]:
    store = blobs if blobs is not None else InMemoryBlobStore()
    service = AssetMutationService(
        SqlAlchemyAssetUnitOfWorkFactory(runtime_engine),
        store,
        clock=_clock,
    )
    return service, store


def _prepared(blobs: InMemoryBlobStore, payload: bytes = PAYLOAD) -> PreparedBlob:
    info = blobs.create(storage_key=uuid7().hex, source=BytesIO(payload))
    return PreparedBlob(
        storage_key=info.storage_key,
        byte_size=info.byte_size,
        sha256=info.sha256,
    )


def _create(service: AssetMutationService):
    tenant, principal, asset_id = uuid7(), uuid7(), AssetId.generate()
    asset = service.create_asset(
        tenant_id=tenant,
        principal_id=principal,
        asset_id=asset_id,
        resource_type=AssetResourceType.IMAGE,
    )
    return asset, tenant, principal


def _register(service, blobs, tenant, principal, asset_id):
    prepared = _prepared(blobs)
    registered = service.register_revision(
        tenant_id=tenant,
        principal_id=principal,
        asset_id=asset_id,
        asset_revision_id=AssetRevisionId.generate(),
        prepared=prepared,
        media_type="image/png",
    )
    return registered, prepared


def _pass_first(service, blobs, tenant, principal, asset_id):
    registered, prepared = _register(service, blobs, tenant, principal, asset_id)
    asset, _ = service.mark_safety_passed(
        tenant_id=tenant,
        principal_id=principal,
        asset_id=asset_id,
        asset_revision_id=registered.revision.asset_revision_id,
        expected_aggregate_revision=ZERO,
    )
    return registered, prepared, asset


def _count_evidence(engine: Engine, asset_id: UUID) -> int:
    with engine.connect() as conn:
        with conn.begin():
            return int(
                conn.execute(
                    text(
                        "SELECT count(*) FROM asset.deletion_evidence "
                        "WHERE asset_id = :id"
                    ),
                    {"id": asset_id},
                ).scalar_one()
            )


def _ref(asset_id: UUID) -> ResourceRef:
    return ResourceRef(
        resource_type="asset.image",
        resource_id=asset_id,
        resource_revision=None,
    )


class UnavailableBlobStore:
    def inspect(self, *, storage_key: str):
        raise BlobStoreUnavailableError("blob store unavailable")

    def create(self, *, storage_key: str, source: object):
        raise AssertionError("create must not be called")

    def delete(self, *, storage_key: str) -> None:
        raise AssertionError("delete must not be called")


class InspectProbe:
    def __init__(self, inner: InMemoryBlobStore, on_inspect=None) -> None:
        self.inner = inner
        self.calls: list[str] = []
        self.on_inspect = on_inspect

    def inspect(self, *, storage_key: str):
        self.calls.append(storage_key)
        if self.on_inspect is not None:
            self.on_inspect()
        return self.inner.inspect(storage_key=storage_key)

    def create(self, *, storage_key: str, source: object):
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
    )
    service.withdraw_asset(
        tenant_id=tenant,
        principal_id=principal,
        asset_id=asset.asset_id,
        expected_aggregate_revision=AssetAggregateRevision(1),
    )
    service.restore_asset(
        tenant_id=tenant,
        principal_id=principal,
        asset_id=asset.asset_id,
        expected_aggregate_revision=AssetAggregateRevision(2),
    )
    service.quarantine_asset(
        tenant_id=tenant,
        principal_id=principal,
        asset_id=asset.asset_id,
        expected_aggregate_revision=AssetAggregateRevision(3),
    )
    head = service.clear_quarantine(
        tenant_id=tenant,
        principal_id=principal,
        asset_id=asset.asset_id,
        expected_aggregate_revision=AssetAggregateRevision(4),
    )
    assert int(head.aggregate_revision) == 5
    assert head.current_revision is None
    return tenant, principal, asset.asset_id, target, historical, head


class TestPostgresCreateAndRegister:
    def test_create_persists_initial_authority_state(self, runtime_engine) -> None:
        service, _ = _service(runtime_engine)
        asset, tenant, principal = _create(service)
        factory = SqlAlchemyAssetUnitOfWorkFactory(runtime_engine)
        with factory(tenant) as uow:
            loaded = uow.assets.get(asset.asset_id)
        assert loaded is not None
        assert loaded.lifecycle is AssetLifecycle.ACTIVE
        assert loaded.quarantine_state is AssetQuarantineState.CLEAR
        assert loaded.current_revision is None
        assert int(loaded.aggregate_revision) == 0
        assert loaded.created_at.tzinfo is not None
        assert loaded.created_by_principal_id == principal
        replayed = service.create_asset(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset.asset_id,
            resource_type=AssetResourceType.IMAGE,
        )
        assert int(replayed.aggregate_revision) == 0

    def test_cross_tenant_create_and_read_are_isolated(
        self, runtime_engine
    ) -> None:
        service, _ = _service(runtime_engine)
        asset, tenant, principal = _create(service)
        other = uuid7()
        with pytest.raises(AssetConflict):
            service.create_asset(
                tenant_id=other,
                principal_id=principal,
                asset_id=asset.asset_id,
                resource_type=AssetResourceType.IMAGE,
            )
        factory = SqlAlchemyAssetUnitOfWorkFactory(runtime_engine)
        with factory(other) as uow:
            assert uow.assets.get(asset.asset_id) is None
        with pytest.raises(AssetNotFound):
            service.withdraw_asset(
                tenant_id=other,
                principal_id=principal,
                asset_id=asset.asset_id,
                expected_aggregate_revision=ZERO,
            )

    def test_concurrent_registrations_allocate_unique_numbers(
        self, runtime_engine
    ) -> None:
        service, blobs = _service(runtime_engine)
        asset, tenant, principal = _create(service)
        prepared_a = _prepared(blobs)
        prepared_b = _prepared(blobs)

        def _one(prepared: PreparedBlob):
            return service.register_revision(
                tenant_id=tenant,
                principal_id=principal,
                asset_id=asset.asset_id,
                asset_revision_id=AssetRevisionId.generate(),
                prepared=prepared,
                media_type="image/png",
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(_one, prepared_a)
            second = pool.submit(_one, prepared_b)
            numbers = {
                int(first.result().revision.revision_number),
                int(second.result().revision.revision_number),
            }
        assert numbers == {1, 2}
        factory = SqlAlchemyAssetUnitOfWorkFactory(runtime_engine)
        with factory(tenant) as uow:
            head = uow.assets.get(asset.asset_id)
            assert head is not None
            assert head.current_revision is None
            assert int(head.aggregate_revision) == 0

    def test_registration_does_not_compensate_with_delete(
        self, runtime_engine
    ) -> None:
        service, blobs = _service(runtime_engine)
        asset, tenant, principal = _create(service)
        service.delete_asset(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset.asset_id,
            expected_aggregate_revision=ZERO,
        )
        prepared = _prepared(blobs)
        with pytest.raises(AssetTransitionRejected):
            service.register_revision(
                tenant_id=tenant,
                principal_id=principal,
                asset_id=asset.asset_id,
                asset_revision_id=AssetRevisionId.generate(),
                prepared=prepared,
                media_type="image/png",
            )
        assert blobs.delete_calls == []
        assert blobs.payload(prepared.storage_key) == PAYLOAD


class TestPostgresActivation:
    def test_activation_and_b4_withdrawn_quarantined(
        self, runtime_engine
    ) -> None:
        service, blobs = _service(runtime_engine)
        asset, tenant, principal = _create(service)
        registered, _, passed = _pass_first(
            service, blobs, tenant, principal, asset.asset_id
        )
        withdrawn = service.withdraw_asset(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset.asset_id,
            expected_aggregate_revision=passed.aggregate_revision,
        )
        activated = service.activate_revision(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset.asset_id,
            resource_type=AssetResourceType.IMAGE,
            revision_number=registered.revision.revision_number,
            expected_aggregate_revision=withdrawn.aggregate_revision,
        )
        assert activated.current_revision == registered.revision.revision_number
        authority = PostgresAssetUseAuthority(runtime_engine, blobs, clock=_clock)
        withdrawn_use = authority.assess_use(
            tenant_id=tenant,
            principal_id=principal,
            resource_ref=_ref(asset.asset_id.value),
        )
        assert withdrawn_use.reason_code is AssetUseRejectionReason.WITHDRAWN
        restored = service.restore_asset(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset.asset_id,
            expected_aggregate_revision=activated.aggregate_revision,
        )
        quarantined = service.quarantine_asset(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset.asset_id,
            expected_aggregate_revision=restored.aggregate_revision,
        )
        second_key = _prepared(blobs)
        second = service.register_revision(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset.asset_id,
            asset_revision_id=AssetRevisionId.generate(),
            prepared=second_key,
            media_type="image/png",
        )
        passed_second, _ = service.mark_safety_passed(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset.asset_id,
            asset_revision_id=second.revision.asset_revision_id,
            expected_aggregate_revision=quarantined.aggregate_revision,
        )
        activated_q = service.activate_revision(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset.asset_id,
            resource_type=AssetResourceType.IMAGE,
            revision_number=second.revision.revision_number,
            expected_aggregate_revision=passed_second.aggregate_revision,
        )
        assert activated_q.quarantine_state is AssetQuarantineState.QUARANTINED
        quarantined_use = authority.assess_use(
            tenant_id=tenant,
            principal_id=principal,
            resource_ref=_ref(asset.asset_id.value),
        )
        assert quarantined_use.reason_code is AssetUseRejectionReason.QUARANTINED

    def test_no_write_lock_held_during_inspect(self, runtime_engine) -> None:
        service, blobs = _service(runtime_engine)
        asset, tenant, principal = _create(service)
        registered, _, passed = _pass_first(
            service, blobs, tenant, principal, asset.asset_id
        )
        probe = {"acquired": False, "error": None}

        class LockProbe:
            def inspect(self, *, storage_key: str):
                try:
                    with runtime_engine.connect() as conn:
                        trans = conn.begin()
                        conn.execute(
                            text("SELECT set_config('aieos.tenant_id', :tid, true)"),
                            {"tid": str(tenant)},
                        )
                        conn.execute(text("SET LOCAL lock_timeout = '250ms'"))
                        conn.execute(
                            text(
                                "SELECT asset_id FROM asset.assets "
                                "WHERE asset_id = :id FOR UPDATE"
                            ),
                            {"id": asset.asset_id.value},
                        )
                        probe["acquired"] = True
                        trans.rollback()
                except Exception as exc:  # noqa: BLE001 — test records lock failure
                    probe["error"] = exc
                    raise
                return blobs.inspect(storage_key=storage_key)

            def create(self, *, storage_key: str, source: object):
                raise AssertionError("create must not be called")

            def delete(self, *, storage_key: str) -> None:
                raise AssertionError("delete must not be called")

        probed = AssetMutationService(
            SqlAlchemyAssetUnitOfWorkFactory(runtime_engine),
            LockProbe(),
            clock=_clock,
        )
        probed.activate_revision(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset.asset_id,
            resource_type=AssetResourceType.IMAGE,
            revision_number=registered.revision.revision_number,
            expected_aggregate_revision=passed.aggregate_revision,
        )
        assert probe["acquired"] is True
        assert probe["error"] is None

    def test_governing_race_after_inspect_conflicts(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        service, blobs = _service(runtime_engine)
        asset, tenant, principal = _create(service)
        registered, _, passed = _pass_first(
            service, blobs, tenant, principal, asset.asset_id
        )

        class MutatingInspect:
            def inspect(self, *, storage_key: str):
                info = blobs.inspect(storage_key=storage_key)
                with bootstrap_engine.begin() as conn:
                    conn.execute(
                        text(
                            """
                            UPDATE asset.assets
                            SET aggregate_revision = aggregate_revision + 1
                            WHERE asset_id = :id
                            """
                        ),
                        {"id": asset.asset_id.value},
                    )
                return info

            def create(self, *, storage_key: str, source: object):
                raise AssertionError("create must not be called")

            def delete(self, *, storage_key: str) -> None:
                raise AssertionError("delete must not be called")

        racing = AssetMutationService(
            SqlAlchemyAssetUnitOfWorkFactory(runtime_engine),
            MutatingInspect(),
            clock=_clock,
        )
        with pytest.raises(AssetConflict):
            racing.activate_revision(
                tenant_id=tenant,
                principal_id=principal,
                asset_id=asset.asset_id,
                resource_type=AssetResourceType.IMAGE,
                revision_number=registered.revision.revision_number,
                expected_aggregate_revision=passed.aggregate_revision,
            )
        factory = SqlAlchemyAssetUnitOfWorkFactory(runtime_engine)
        with factory(tenant) as uow:
            head = uow.assets.get(asset.asset_id)
            assert head is not None
            assert head.current_revision is None

    def test_bytes_purged_and_unavailable_fail_closed(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        service, blobs = _service(runtime_engine)
        asset, tenant, principal = _create(service)
        registered, _, passed = _pass_first(
            service, blobs, tenant, principal, asset.asset_id
        )
        with bootstrap_engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE asset.asset_revision_states
                    SET bytes_purged = true
                    WHERE asset_revision_id = :id
                    """
                ),
                {"id": registered.revision.asset_revision_id.value},
            )
        with pytest.raises(AssetActivationRejected) as purged:
            service.activate_revision(
                tenant_id=tenant,
                principal_id=principal,
                asset_id=asset.asset_id,
                resource_type=AssetResourceType.IMAGE,
                revision_number=registered.revision.revision_number,
                expected_aggregate_revision=passed.aggregate_revision,
            )
        assert purged.value.reason == "bytes_purged"
        with bootstrap_engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE asset.asset_revision_states
                    SET bytes_purged = false
                    WHERE asset_revision_id = :id
                    """
                ),
                {"id": registered.revision.asset_revision_id.value},
            )
        down = AssetMutationService(
            SqlAlchemyAssetUnitOfWorkFactory(runtime_engine),
            UnavailableBlobStore(),
            clock=_clock,
        )
        with pytest.raises(BlobStoreUnavailableError):
            down.activate_revision(
                tenant_id=tenant,
                principal_id=principal,
                asset_id=asset.asset_id,
                resource_type=AssetResourceType.IMAGE,
                revision_number=registered.revision.revision_number,
                expected_aggregate_revision=passed.aggregate_revision,
            )
        factory = SqlAlchemyAssetUnitOfWorkFactory(runtime_engine)
        with factory(tenant) as uow:
            head = uow.assets.get(asset.asset_id)
            assert head is not None
            assert head.current_revision is None


class TestPostgresLifecycleSafetyRls:
    def test_delete_retains_revision_and_writes_no_purge(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        service, blobs = _service(runtime_engine)
        asset, tenant, principal = _create(service)
        registered, _, passed = _pass_first(
            service, blobs, tenant, principal, asset.asset_id
        )
        activated = service.activate_revision(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset.asset_id,
            resource_type=AssetResourceType.IMAGE,
            revision_number=registered.revision.revision_number,
            expected_aggregate_revision=passed.aggregate_revision,
        )
        deleted = service.delete_asset(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset.asset_id,
            expected_aggregate_revision=activated.aggregate_revision,
        )
        assert deleted.lifecycle is AssetLifecycle.DELETED
        assert deleted.current_revision == registered.revision.revision_number
        assert blobs.delete_calls == []
        assert _count_evidence(bootstrap_engine, asset.asset_id.value) == 0
        factory = SqlAlchemyAssetUnitOfWorkFactory(runtime_engine)
        with factory(tenant) as uow:
            state = uow.revision_states.get(registered.revision.asset_revision_id)
            assert state is not None
            assert state.bytes_purged is False
            revision = uow.revisions.get(registered.revision.asset_revision_id)
            assert revision is not None
            assert revision.sha256 == SHA

    def test_pending_safety_may_finalize_after_delete(self, runtime_engine) -> None:
        service, blobs = _service(runtime_engine)
        asset, tenant, principal = _create(service)
        registered, _ = _register(service, blobs, tenant, principal, asset.asset_id)
        deleted = service.delete_asset(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset.asset_id,
            expected_aggregate_revision=ZERO,
        )
        finalized, state = service.mark_safety_passed(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset.asset_id,
            asset_revision_id=registered.revision.asset_revision_id,
            expected_aggregate_revision=deleted.aggregate_revision,
        )
        assert state.safety_state is AssetRevisionSafetyState.PASSED
        assert finalized.lifecycle is AssetLifecycle.DELETED
        assert int(finalized.aggregate_revision) == int(deleted.aggregate_revision) + 1

    def test_pooled_tenant_isolation_and_missing_tenant_fails_closed(
        self, runtime_engine
    ) -> None:
        service, _ = _service(runtime_engine)
        asset_a, tenant_a, principal_a = _create(service)
        asset_b, tenant_b, principal_b = _create(service)
        factory = SqlAlchemyAssetUnitOfWorkFactory(runtime_engine)
        with factory(tenant_a) as uow:
            assert uow.assets.get(asset_a.asset_id) is not None
            assert uow.assets.get(asset_b.asset_id) is None
        with factory(tenant_b) as uow:
            assert uow.assets.get(asset_b.asset_id) is not None
            assert uow.assets.get(asset_a.asset_id) is None
        _ = principal_a, principal_b
        with runtime_engine.connect() as conn:
            trans = conn.begin()
            repo = SqlAlchemyAssetWriteRepository(conn, tenant_a)
            with pytest.raises(AssetPersistenceFailed):
                repo.get(asset_a.asset_id)
            trans.rollback()

    def test_rollback_leaves_zero_partial_mutation(
        self, runtime_engine, monkeypatch
    ) -> None:
        service, blobs = _service(runtime_engine)
        asset, tenant, principal = _create(service)
        prepared = _prepared(blobs)

        def boom(self, state) -> None:  # noqa: ANN001
            raise RuntimeError("injected failure")

        monkeypatch.setattr(
            SqlAlchemyAssetRevisionStateWriteRepository, "insert", boom
        )
        with pytest.raises(RuntimeError, match="injected failure"):
            service.register_revision(
                tenant_id=tenant,
                principal_id=principal,
                asset_id=asset.asset_id,
                asset_revision_id=AssetRevisionId.generate(),
                prepared=prepared,
                media_type="image/png",
            )
        factory = SqlAlchemyAssetUnitOfWorkFactory(runtime_engine)
        with factory(tenant) as uow:
            assert uow.revisions.max_revision_number(asset.asset_id) == 0
            head = uow.assets.get(asset.asset_id)
            assert head is not None
            assert int(head.aggregate_revision) == 0
        assert blobs.delete_calls == []


class TestPostgresExpectedRevisionStability:
    def test_future_expected_revision_conflicts_without_inspect(
        self, runtime_engine
    ) -> None:
        writer, blobs = _service(runtime_engine)
        tenant, principal, asset_id, target, _, _ = _activatable_at_five(
            writer, blobs
        )
        probe = InspectProbe(blobs)
        service = AssetMutationService(
            SqlAlchemyAssetUnitOfWorkFactory(runtime_engine),
            probe,
            clock=_clock,
        )
        with pytest.raises(AssetConflict):
            service.activate_revision(
                tenant_id=tenant,
                principal_id=principal,
                asset_id=asset_id,
                resource_type=AssetResourceType.IMAGE,
                revision_number=target.revision.revision_number,
                expected_aggregate_revision=AssetAggregateRevision(6),
            )
        assert probe.calls == []
        factory = SqlAlchemyAssetUnitOfWorkFactory(runtime_engine)
        with factory(tenant) as uow:
            loaded = uow.assets.get(asset_id)
        assert loaded is not None
        assert loaded.current_revision is None
        assert int(loaded.aggregate_revision) == 5

    def test_future_expected_does_not_activate_when_inspect_would_advance_aggregate(
        self, runtime_engine
    ) -> None:
        writer, blobs = _service(runtime_engine)
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
            )

        probe = InspectProbe(blobs, on_inspect=bump_historical)
        service = AssetMutationService(
            SqlAlchemyAssetUnitOfWorkFactory(runtime_engine),
            probe,
            clock=_clock,
        )
        with pytest.raises(AssetConflict):
            service.activate_revision(
                tenant_id=tenant,
                principal_id=principal,
                asset_id=asset_id,
                resource_type=AssetResourceType.IMAGE,
                revision_number=target.revision.revision_number,
                expected_aggregate_revision=AssetAggregateRevision(6),
            )
        assert probe.calls == []
        factory = SqlAlchemyAssetUnitOfWorkFactory(runtime_engine)
        with factory(tenant) as uow:
            loaded = uow.assets.get(asset_id)
            state = uow.revision_states.get(historical.revision.asset_revision_id)
        assert loaded is not None
        assert loaded.current_revision is None
        assert int(loaded.aggregate_revision) == 5
        assert state is not None
        assert state.safety_state is AssetRevisionSafetyState.PENDING

    def test_aggregate_change_during_inspect_conflicts(self, runtime_engine) -> None:
        writer, blobs = _service(runtime_engine)
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
            )

        probe = InspectProbe(blobs, on_inspect=bump_historical)
        service = AssetMutationService(
            SqlAlchemyAssetUnitOfWorkFactory(runtime_engine),
            probe,
            clock=_clock,
        )
        with pytest.raises(AssetConflict):
            service.activate_revision(
                tenant_id=tenant,
                principal_id=principal,
                asset_id=asset_id,
                resource_type=AssetResourceType.IMAGE,
                revision_number=target.revision.revision_number,
                expected_aggregate_revision=head.aggregate_revision,
            )
        assert probe.calls == [target.revision.storage_key]
        factory = SqlAlchemyAssetUnitOfWorkFactory(runtime_engine)
        with factory(tenant) as uow:
            loaded = uow.assets.get(asset_id)
        assert loaded is not None
        assert loaded.current_revision is None
        assert int(loaded.aggregate_revision) == 6

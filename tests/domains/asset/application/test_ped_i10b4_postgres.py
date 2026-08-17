"""PED-I10B4 PostgreSQL RLS, stability, and Content adapter integration tests."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from io import BytesIO
from uuid import UUID, uuid7

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.domains.asset.infrastructure.persistence.authority_reads import (
    fetch_typed_asset,
)
from aieos.domains.asset.infrastructure.persistence.postgres_use_authority import (
    PostgresAssetUseAuthority,
)
from aieos.domains.content.application.asset_authority_adapters import (
    AssetAuthorityCurrentGovernanceAdapter,
    AssetAuthorityReferenceValidationAdapter,
)
from aieos.domains.content.application.errors import (
    AssetReferenceValidationFailed,
    PublicationAssetValidationFailed,
)
from aieos.domains.content.domain.identities import ContentId, ContentVersionId
from aieos.domains.content.domain.version_asset_ref import VersionAssetRef
from aieos.platform.governance.errors import GovernanceUnavailableError
from aieos.platform.resources import ResourceRef
from aieos.platform.resources.asset_use import AssetUseRejectionReason
from tests.domains.asset.application.fakes import InMemoryBlobStore

pytestmark = pytest.mark.ped_i10b4

PAYLOAD = b"asset-bytes-v1"
SHA = hashlib.sha256(PAYLOAD).hexdigest()
SIZE = len(PAYLOAD)
HANDLED = frozenset({"asset.image", "asset.document"})
FIXED = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)


def _now() -> datetime:
    return datetime(2026, 8, 17, 12, 0, tzinfo=UTC)


def _insert_asset(
    conn,
    *,
    tenant_id: UUID,
    asset_id: UUID,
    resource_type: str = "asset.image",
    lifecycle: str = "active",
    quarantine_state: str = "clear",
    current_revision: int | None = None,
    aggregate_revision: int = 0,
) -> None:
    conn.execute(
        text(
            """
            INSERT INTO asset.assets (
                tenant_id, asset_id, resource_type, lifecycle, quarantine_state,
                current_revision, aggregate_revision, created_at,
                created_by_principal_id
            ) VALUES (
                :tenant_id, :asset_id, :resource_type, :lifecycle,
                :quarantine_state, :current_revision, :aggregate_revision,
                :created_at, :created_by
            )
            """
        ),
        {
            "tenant_id": tenant_id,
            "asset_id": asset_id,
            "resource_type": resource_type,
            "lifecycle": lifecycle,
            "quarantine_state": quarantine_state,
            "current_revision": current_revision,
            "aggregate_revision": aggregate_revision,
            "created_at": _now(),
            "created_by": uuid7(),
        },
    )


def _insert_revision(
    conn,
    *,
    tenant_id: UUID,
    asset_id: UUID,
    asset_revision_id: UUID,
    revision_number: int,
    resource_type: str = "asset.image",
    storage_key: str,
    byte_size: int = SIZE,
    sha256: str = SHA,
) -> None:
    conn.execute(
        text(
            """
            INSERT INTO asset.asset_revisions (
                asset_revision_id, tenant_id, asset_id, revision_number,
                resource_type, storage_key, media_type, byte_size, sha256,
                created_at, created_by_principal_id
            ) VALUES (
                :asset_revision_id, :tenant_id, :asset_id, :revision_number,
                :resource_type, :storage_key, :media_type, :byte_size, :sha256,
                :created_at, :created_by
            )
            """
        ),
        {
            "asset_revision_id": asset_revision_id,
            "tenant_id": tenant_id,
            "asset_id": asset_id,
            "revision_number": revision_number,
            "resource_type": resource_type,
            "storage_key": storage_key,
            "media_type": "image/png",
            "byte_size": byte_size,
            "sha256": sha256,
            "created_at": _now(),
            "created_by": uuid7(),
        },
    )


def _insert_state(
    conn,
    *,
    tenant_id: UUID,
    asset_id: UUID,
    asset_revision_id: UUID,
    revision_number: int,
    safety_state: str = "passed",
    bytes_purged: bool = False,
) -> None:
    conn.execute(
        text(
            """
            INSERT INTO asset.asset_revision_states (
                asset_revision_id, tenant_id, asset_id, revision_number,
                safety_state, bytes_purged, updated_at
            ) VALUES (
                :asset_revision_id, :tenant_id, :asset_id, :revision_number,
                :safety_state, :bytes_purged, :updated_at
            )
            """
        ),
        {
            "asset_revision_id": asset_revision_id,
            "tenant_id": tenant_id,
            "asset_id": asset_id,
            "revision_number": revision_number,
            "safety_state": safety_state,
            "bytes_purged": bytes_purged,
            "updated_at": _now(),
        },
    )


def _set_current(conn, *, asset_id: UUID, current_revision: int, aggregate_revision: int) -> None:
    conn.execute(
        text(
            """
            UPDATE asset.assets
            SET current_revision = :rev, aggregate_revision = :agg
            WHERE asset_id = :id
            """
        ),
        {"rev": current_revision, "agg": aggregate_revision, "id": asset_id},
    )


def _seed_revision(
    bootstrap_engine: Engine,
    *,
    tenant_id: UUID,
    asset_id: UUID,
    resource_type: str = "asset.image",
    lifecycle: str = "active",
    quarantine_state: str = "clear",
    aggregate_revision: int = 3,
    revisions: list[dict[str, object]] | None = None,
) -> InMemoryBlobStore:
    blobs = InMemoryBlobStore()
    if revisions is None:
        key = uuid7().hex
        blobs.create(storage_key=key, source=BytesIO(PAYLOAD))
        revisions = [
            {
                "revision_number": 1,
                "storage_key": key,
                "safety_state": "passed",
                "bytes_purged": False,
                "current": True,
            }
        ]
    with bootstrap_engine.begin() as conn:
        _insert_asset(
            conn,
            tenant_id=tenant_id,
            asset_id=asset_id,
            resource_type=resource_type,
            lifecycle=lifecycle,
            quarantine_state=quarantine_state,
            current_revision=None,
            aggregate_revision=0,
        )
        current = 1
        for spec in revisions:
            revision_id = uuid7()
            number = int(spec["revision_number"])  # type: ignore[arg-type]
            storage_key = str(spec["storage_key"])
            if storage_key not in blobs._payloads:
                blobs.create(storage_key=storage_key, source=BytesIO(PAYLOAD))
            _insert_revision(
                conn,
                tenant_id=tenant_id,
                asset_id=asset_id,
                asset_revision_id=revision_id,
                revision_number=number,
                resource_type=resource_type,
                storage_key=storage_key,
                byte_size=int(spec.get("byte_size", SIZE)),  # type: ignore[arg-type]
                sha256=str(spec.get("sha256", SHA)),
            )
            if spec.get("with_state", True):
                _insert_state(
                    conn,
                    tenant_id=tenant_id,
                    asset_id=asset_id,
                    asset_revision_id=revision_id,
                    revision_number=number,
                    safety_state=str(spec.get("safety_state", "passed")),
                    bytes_purged=bool(spec.get("bytes_purged", False)),
                )
            if spec.get("current"):
                current = number
        _set_current(
            conn,
            asset_id=asset_id,
            current_revision=current,
            aggregate_revision=aggregate_revision,
        )
    blobs.inspect_calls.clear()
    return blobs


def _authority(
    runtime_engine: Engine, blobs: InMemoryBlobStore
) -> PostgresAssetUseAuthority:
    return PostgresAssetUseAuthority(
        runtime_engine, blobs, clock=lambda: FIXED
    )


def _ref(
    asset_id: UUID,
    *,
    resource_type: str = "asset.image",
    revision: int | None = None,
) -> ResourceRef:
    return ResourceRef(
        resource_type=resource_type,
        resource_id=asset_id,
        resource_revision=revision,
    )


class MutatingOnInspect:
    def __init__(self, inner: InMemoryBlobStore, bootstrap: Engine, sql: str, params: dict) -> None:
        self.inner = inner
        self.bootstrap = bootstrap
        self.sql = sql
        self.params = params
        self.inspect_calls: list[str] = []

    def inspect(self, *, storage_key: str):
        self.inspect_calls.append(storage_key)
        info = self.inner.inspect(storage_key=storage_key)
        with self.bootstrap.begin() as conn:
            conn.execute(text(self.sql), self.params)
        return info


class TestPostgresIdentityAndRls:
    def test_usable_asset(self, runtime_engine, bootstrap_engine) -> None:
        tenant_id, asset_id, principal = uuid7(), uuid7(), uuid7()
        blobs = _seed_revision(bootstrap_engine, tenant_id=tenant_id, asset_id=asset_id)
        result = _authority(runtime_engine, blobs).assess_use(
            tenant_id=tenant_id, principal_id=principal, resource_ref=_ref(asset_id)
        )
        assert result.usable is True
        assert result.reason_code is None
        assert result.authority_revision == 3
        assert result.observed_at == FIXED

    def test_unknown_asset_not_found(self, runtime_engine) -> None:
        result = _authority(runtime_engine, InMemoryBlobStore()).assess_use(
            tenant_id=uuid7(),
            principal_id=uuid7(),
            resource_ref=_ref(uuid7()),
        )
        assert result.reason_code is AssetUseRejectionReason.NOT_FOUND
        assert result.authority_revision is None

    def test_cross_tenant_hidden_by_rls_is_not_found(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        owner, other, asset_id = uuid7(), uuid7(), uuid7()
        blobs = _seed_revision(bootstrap_engine, tenant_id=owner, asset_id=asset_id)
        result = _authority(runtime_engine, blobs).assess_use(
            tenant_id=other, principal_id=uuid7(), resource_ref=_ref(asset_id)
        )
        assert result.reason_code is AssetUseRejectionReason.NOT_FOUND
        assert result.authority_revision is None
        assert result.reason_code is not AssetUseRejectionReason.TENANT_INACCESSIBLE

    def test_resource_type_mismatch_not_found(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id, asset_id = uuid7(), uuid7()
        blobs = _seed_revision(bootstrap_engine, tenant_id=tenant_id, asset_id=asset_id)
        result = _authority(runtime_engine, blobs).assess_use(
            tenant_id=tenant_id,
            principal_id=uuid7(),
            resource_ref=_ref(asset_id, resource_type="asset.document"),
        )
        assert result.reason_code is AssetUseRejectionReason.NOT_FOUND
        assert result.authority_revision is None

    def test_pooled_connection_tenant_isolation(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_a, tenant_b, asset_a, asset_b = uuid7(), uuid7(), uuid7(), uuid7()
        blobs_a = _seed_revision(bootstrap_engine, tenant_id=tenant_a, asset_id=asset_a)
        blobs_b = _seed_revision(bootstrap_engine, tenant_id=tenant_b, asset_id=asset_b)
        blobs = InMemoryBlobStore()
        blobs._payloads.update(blobs_a._payloads)
        blobs._payloads.update(blobs_b._payloads)
        authority = _authority(runtime_engine, blobs)
        first = authority.assess_use(
            tenant_id=tenant_a, principal_id=uuid7(), resource_ref=_ref(asset_a)
        )
        assert first.usable is True
        leaked = authority.assess_use(
            tenant_id=tenant_b, principal_id=uuid7(), resource_ref=_ref(asset_a)
        )
        assert leaked.reason_code is AssetUseRejectionReason.NOT_FOUND
        second = authority.assess_use(
            tenant_id=tenant_b, principal_id=uuid7(), resource_ref=_ref(asset_b)
        )
        assert second.usable is True

    def test_missing_tenant_context_fails_closed(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id, asset_id = uuid7(), uuid7()
        _seed_revision(bootstrap_engine, tenant_id=tenant_id, asset_id=asset_id)
        with runtime_engine.connect() as conn:
            with conn.begin():
                with pytest.raises(
                    GovernanceUnavailableError, match="governance unavailable"
                ):
                    fetch_typed_asset(
                        conn,
                        tenant_id=tenant_id,
                        asset_id=asset_id,
                        resource_type="asset.image",
                    )


class TestPostgresRevisionAndPhysical:
    def test_pinned_revision_evaluated_exactly(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id, asset_id = uuid7(), uuid7()
        key1, key2 = uuid7().hex, uuid7().hex
        blobs = _seed_revision(
            bootstrap_engine,
            tenant_id=tenant_id,
            asset_id=asset_id,
            revisions=[
                {
                    "revision_number": 1,
                    "storage_key": key1,
                    "safety_state": "failed",
                    "current": False,
                },
                {
                    "revision_number": 2,
                    "storage_key": key2,
                    "safety_state": "passed",
                    "current": True,
                },
            ],
        )
        failed = _authority(runtime_engine, blobs).assess_use(
            tenant_id=tenant_id,
            principal_id=uuid7(),
            resource_ref=_ref(asset_id, revision=1),
        )
        assert failed.reason_code is AssetUseRejectionReason.SAFETY_FAILED
        passed = _authority(runtime_engine, blobs).assess_use(
            tenant_id=tenant_id,
            principal_id=uuid7(),
            resource_ref=_ref(asset_id, revision=2),
        )
        assert passed.usable is True

    def test_pinned_revision_absent(self, runtime_engine, bootstrap_engine) -> None:
        tenant_id, asset_id = uuid7(), uuid7()
        blobs = _seed_revision(bootstrap_engine, tenant_id=tenant_id, asset_id=asset_id)
        result = _authority(runtime_engine, blobs).assess_use(
            tenant_id=tenant_id,
            principal_id=uuid7(),
            resource_ref=_ref(asset_id, revision=9),
        )
        assert result.reason_code is AssetUseRejectionReason.REVISION_NOT_FOUND
        assert result.authority_revision == 3

    def test_unpinned_current_revision_null(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id, asset_id = uuid7(), uuid7()
        with bootstrap_engine.begin() as conn:
            _insert_asset(
                conn,
                tenant_id=tenant_id,
                asset_id=asset_id,
                current_revision=None,
                aggregate_revision=2,
            )
        result = _authority(runtime_engine, InMemoryBlobStore()).assess_use(
            tenant_id=tenant_id, principal_id=uuid7(), resource_ref=_ref(asset_id)
        )
        assert result.reason_code is AssetUseRejectionReason.REVISION_NOT_FOUND
        assert result.authority_revision == 2

    def test_bytes_purged_skips_inspect(self, runtime_engine, bootstrap_engine) -> None:
        tenant_id, asset_id = uuid7(), uuid7()
        key = uuid7().hex
        blobs = _seed_revision(
            bootstrap_engine,
            tenant_id=tenant_id,
            asset_id=asset_id,
            revisions=[
                {
                    "revision_number": 1,
                    "storage_key": key,
                    "bytes_purged": True,
                    "current": True,
                }
            ],
        )
        result = _authority(runtime_engine, blobs).assess_use(
            tenant_id=tenant_id, principal_id=uuid7(), resource_ref=_ref(asset_id)
        )
        assert result.reason_code is AssetUseRejectionReason.BYTES_PURGED
        assert blobs.inspect_calls == []

    def test_missing_revision_state_fails_closed(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id, asset_id = uuid7(), uuid7()
        key = uuid7().hex
        blobs = _seed_revision(
            bootstrap_engine,
            tenant_id=tenant_id,
            asset_id=asset_id,
            revisions=[
                {
                    "revision_number": 1,
                    "storage_key": key,
                    "with_state": False,
                    "current": True,
                }
            ],
        )
        with pytest.raises(GovernanceUnavailableError, match="governance unavailable"):
            _authority(runtime_engine, blobs).assess_use(
                tenant_id=tenant_id, principal_id=uuid7(), resource_ref=_ref(asset_id)
            )

    def test_cross_store_race_does_not_return_stale_usable(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id, asset_id = uuid7(), uuid7()
        blobs = _seed_revision(bootstrap_engine, tenant_id=tenant_id, asset_id=asset_id)
        mutating = MutatingOnInspect(
            blobs,
            bootstrap_engine,
            "UPDATE asset.assets SET lifecycle = 'withdrawn', "
            "aggregate_revision = aggregate_revision + 1 WHERE asset_id = :id",
            {"id": asset_id},
        )
        result = PostgresAssetUseAuthority(
            runtime_engine, mutating, clock=lambda: FIXED
        ).assess_use(
            tenant_id=tenant_id, principal_id=uuid7(), resource_ref=_ref(asset_id)
        )
        assert result.usable is False
        assert result.reason_code is AssetUseRejectionReason.WITHDRAWN

    def test_unpinned_current_revision_change_during_inspect(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id, asset_id = uuid7(), uuid7()
        key1, key2 = uuid7().hex, uuid7().hex
        blobs = _seed_revision(
            bootstrap_engine,
            tenant_id=tenant_id,
            asset_id=asset_id,
            revisions=[
                {
                    "revision_number": 1,
                    "storage_key": key1,
                    "safety_state": "passed",
                    "current": False,
                },
                {
                    "revision_number": 2,
                    "storage_key": key2,
                    "safety_state": "pending",
                    "current": True,
                },
            ],
        )
        with bootstrap_engine.begin() as conn:
            _set_current(conn, asset_id=asset_id, current_revision=1, aggregate_revision=3)
        mutating = MutatingOnInspect(
            blobs,
            bootstrap_engine,
            "UPDATE asset.assets SET current_revision = 2, "
            "aggregate_revision = aggregate_revision + 1 WHERE asset_id = :id",
            {"id": asset_id},
        )
        result = PostgresAssetUseAuthority(
            runtime_engine, mutating, clock=lambda: FIXED
        ).assess_use(
            tenant_id=tenant_id, principal_id=uuid7(), resource_ref=_ref(asset_id)
        )
        assert result.reason_code is AssetUseRejectionReason.SAFETY_PENDING
        assert mutating.inspect_calls == [key1]

    def test_persistent_churn_fails_closed(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id, asset_id = uuid7(), uuid7()
        blobs = _seed_revision(bootstrap_engine, tenant_id=tenant_id, asset_id=asset_id)
        mutating = MutatingOnInspect(
            blobs,
            bootstrap_engine,
            "UPDATE asset.assets SET aggregate_revision = aggregate_revision + 1 "
            "WHERE asset_id = :id",
            {"id": asset_id},
        )
        with pytest.raises(GovernanceUnavailableError, match="governance unavailable"):
            PostgresAssetUseAuthority(
                runtime_engine, mutating, clock=lambda: FIXED, max_positive_attempts=3
            ).assess_use(
                tenant_id=tenant_id, principal_id=uuid7(), resource_ref=_ref(asset_id)
            )


class TestContentAdapters:
    def test_reference_validation_adapter_with_concrete_authority(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id, asset_id, principal = uuid7(), uuid7(), uuid7()
        blobs = _seed_revision(bootstrap_engine, tenant_id=tenant_id, asset_id=asset_id)
        adapter = AssetAuthorityReferenceValidationAdapter(
            _authority(runtime_engine, blobs), handled_resource_types=HANDLED
        )
        adapter.validate_binding(
            tenant_id=tenant_id,
            principal_id=principal,
            resource_ref=_ref(asset_id),
        )
        with pytest.raises(AssetReferenceValidationFailed, match="asset reference invalid"):
            adapter.validate_binding(
                tenant_id=tenant_id,
                principal_id=principal,
                resource_ref=_ref(uuid7()),
            )

    def test_current_governance_adapter_with_concrete_authority(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id, asset_id, principal = uuid7(), uuid7(), uuid7()
        blobs = _seed_revision(bootstrap_engine, tenant_id=tenant_id, asset_id=asset_id)
        adapter = AssetAuthorityCurrentGovernanceAdapter(
            _authority(runtime_engine, blobs), handled_resource_types=HANDLED
        )
        ref = VersionAssetRef(
            tenant_id=tenant_id,
            content_id=ContentId(uuid7()),
            version_id=ContentVersionId(uuid7()),
            resource_ref=_ref(asset_id),
            role="primary",
            ordinal=0,
            required=True,
            created_at=FIXED,
        )
        adapter.validate_current_use(
            tenant_id=tenant_id,
            principal_id=principal,
            content_id=ref.content_id,
            version_id=ref.version_id,
            asset_refs=[ref],
        )

    def test_adapters_do_not_leak_asset_rejection_reasons(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id, asset_id, principal = uuid7(), uuid7(), uuid7()
        blobs = _seed_revision(
            bootstrap_engine,
            tenant_id=tenant_id,
            asset_id=asset_id,
            lifecycle="deleted",
        )
        binding = AssetAuthorityReferenceValidationAdapter(
            _authority(runtime_engine, blobs), handled_resource_types=HANDLED
        )
        with pytest.raises(AssetReferenceValidationFailed) as binding_exc:
            binding.validate_binding(
                tenant_id=tenant_id,
                principal_id=principal,
                resource_ref=_ref(asset_id),
            )
        assert "DELETED" not in str(binding_exc.value)
        assert "BYTES_" not in str(binding_exc.value)
        current = AssetAuthorityCurrentGovernanceAdapter(
            _authority(runtime_engine, blobs), handled_resource_types=HANDLED
        )
        ref = VersionAssetRef(
            tenant_id=tenant_id,
            content_id=ContentId(uuid7()),
            version_id=ContentVersionId(uuid7()),
            resource_ref=_ref(asset_id),
            role="primary",
            ordinal=0,
            required=True,
            created_at=FIXED,
        )
        with pytest.raises(PublicationAssetValidationFailed) as pub_exc:
            current.validate_current_use(
                tenant_id=tenant_id,
                principal_id=principal,
                content_id=ref.content_id,
                version_id=ref.version_id,
                asset_refs=[ref],
            )
        assert "DELETED" not in str(pub_exc.value)
        assert "QUARANTINED" not in str(pub_exc.value)

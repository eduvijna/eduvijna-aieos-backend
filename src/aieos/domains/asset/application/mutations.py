"""NON_PRODUCTION Asset mutation and revision-activation commands (PED-I10B5/B6).

ADR-AIEOS-035 / ADR-AIEOS-036 / ADR-AIEOS-036R1. Not composed into FastAPI,
Temporal, NATS, or runtime. BlobStore is used only to inspect physical bytes
during activation. This module must never invoke the BlobStore physical-delete
operation or mutate bytes_purged to true.
Authorization occurs before the first Asset Unit of Work. Successful
state-changing mutations insert one security.audit_records row in the same
transaction.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from aieos.domains.asset.application.audit import (
    AssetMutationAuditProvenance,
    insert_required_asset_audit,
)
from aieos.domains.asset.application.blob_store import BlobStore
from aieos.domains.asset.application.errors import BlobStoreUnavailableError
from aieos.domains.asset.application.ingest import PreparedBlob
from aieos.domains.asset.application.mutation_errors import (
    AssetActivationRejected,
    AssetConflict,
    AssetIdentityConflict,
    AssetNotFound,
    AssetPersistenceFailed,
    AssetTransitionRejected,
)
from aieos.domains.asset.application.ports import (
    ASSET_CREATE,
    ASSET_LIFECYCLE_MANAGE,
    ASSET_QUARANTINE_MANAGE,
    ASSET_REVISION_ACTIVATE,
    ASSET_REVISION_REGISTER,
    ASSET_SAFETY_DECIDE,
    AssetMutationAuthorizationPort,
    AssetUnitOfWorkFactory,
)
from aieos.domains.asset.domain.asset import Asset
from aieos.domains.asset.domain.identities import (
    AssetAggregateRevision,
    AssetId,
    AssetRevisionId,
    AssetRevisionNumber,
    require_foreign_uuid,
)
from aieos.domains.asset.domain.resource_type import (
    AssetResourceType,
    parse_asset_resource_type,
)
from aieos.domains.asset.domain.revision import AssetRevision, AssetRevisionState
from aieos.domains.asset.domain.state import (
    AssetLifecycle,
    AssetQuarantineState,
    AssetRevisionSafetyState,
)
from aieos.platform.events.models import MutationEventContext
from aieos.platform.security.audit.actions import SecurityAuditAction

_LIFECYCLE_ALLOWED: frozenset[tuple[AssetLifecycle, AssetLifecycle]] = frozenset(
    {
        (AssetLifecycle.ACTIVE, AssetLifecycle.WITHDRAWN),
        (AssetLifecycle.WITHDRAWN, AssetLifecycle.ACTIVE),
        (AssetLifecycle.ACTIVE, AssetLifecycle.DELETED),
        (AssetLifecycle.WITHDRAWN, AssetLifecycle.DELETED),
    }
)
_QUARANTINE_ALLOWED: frozenset[
    tuple[AssetQuarantineState, AssetQuarantineState]
] = frozenset(
    {
        (AssetQuarantineState.CLEAR, AssetQuarantineState.QUARANTINED),
        (AssetQuarantineState.QUARANTINED, AssetQuarantineState.CLEAR),
    }
)
_SAFETY_ALLOWED: frozenset[
    tuple[AssetRevisionSafetyState, AssetRevisionSafetyState]
] = frozenset(
    {
        (AssetRevisionSafetyState.PENDING, AssetRevisionSafetyState.PASSED),
        (AssetRevisionSafetyState.PENDING, AssetRevisionSafetyState.FAILED),
        (AssetRevisionSafetyState.PASSED, AssetRevisionSafetyState.FAILED),
    }
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class RegisteredRevision:
    """Result of registering an immutable revision plus its initial state."""

    revision: AssetRevision
    state: AssetRevisionState


@dataclass(frozen=True, slots=True)
class _ActivationCandidate:
    asset: Asset
    revision: AssetRevision
    state: AssetRevisionState


class AssetMutationService:
    """Application-owned Asset write commands. Tests instantiate this explicitly."""

    def __init__(
        self,
        uow_factory: AssetUnitOfWorkFactory,
        blob_store: BlobStore,
        authorization: AssetMutationAuthorizationPort,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._blob_store = blob_store
        self._authorization = authorization
        self._clock = clock if clock is not None else _utc_now

    def _authorize(
        self,
        *,
        tenant_id: UUID,
        principal_id: UUID,
        capability: str,
        asset_id: AssetId | None,
    ) -> None:
        self._authorization.authorize(
            tenant_id=tenant_id,
            principal_id=principal_id,
            capability=capability,
            asset_id=asset_id,
        )

    def create_asset(
        self,
        *,
        tenant_id: UUID,
        principal_id: UUID,
        asset_id: AssetId,
        resource_type: AssetResourceType | str,
        mutation_event_context: MutationEventContext,
        audit_provenance: AssetMutationAuditProvenance,
    ) -> Asset:
        require_foreign_uuid(tenant_id, label="tenant_id")
        require_foreign_uuid(principal_id, label="principal_id")
        parsed_type = parse_asset_resource_type(resource_type)
        self._authorize(
            tenant_id=tenant_id,
            principal_id=principal_id,
            capability=ASSET_CREATE,
            asset_id=asset_id,
        )
        now = self._clock()
        asset = Asset(
            tenant_id=tenant_id,
            asset_id=asset_id,
            resource_type=parsed_type,
            lifecycle=AssetLifecycle.ACTIVE,
            quarantine_state=AssetQuarantineState.CLEAR,
            current_revision=None,
            aggregate_revision=AssetAggregateRevision(0),
            created_at=now,
            created_by_principal_id=principal_id,
        )
        try:
            with self._uow_factory(tenant_id) as uow:
                existing = uow.assets.get(asset_id)
                if existing is not None:
                    _assert_compatible_create(existing, parsed_type)
                    return existing
                uow.assets.insert(asset)
                insert_required_asset_audit(
                    uow,
                    tenant_id=tenant_id,
                    action=SecurityAuditAction.ASSET_CREATE,
                    resource_type=parsed_type,
                    asset_id=asset_id,
                    resource_revision_before=None,
                    resource_revision_after=0,
                    mutation_event_context=mutation_event_context,
                    audit_provenance=audit_provenance,
                    occurred_at=now,
                )
                uow.commit()
                return asset
        except AssetIdentityConflict:
            return self._recover_create(tenant_id, asset_id, parsed_type)

    def register_revision(
        self,
        *,
        tenant_id: UUID,
        principal_id: UUID,
        asset_id: AssetId,
        asset_revision_id: AssetRevisionId,
        prepared: PreparedBlob,
        media_type: str,
        mutation_event_context: MutationEventContext,
        audit_provenance: AssetMutationAuditProvenance,
    ) -> RegisteredRevision:
        require_foreign_uuid(tenant_id, label="tenant_id")
        require_foreign_uuid(principal_id, label="principal_id")
        self._authorize(
            tenant_id=tenant_id,
            principal_id=principal_id,
            capability=ASSET_REVISION_REGISTER,
            asset_id=asset_id,
        )
        now = self._clock()
        try:
            with self._uow_factory(tenant_id) as uow:
                locked = uow.assets.get_for_update(asset_id)
                if locked is None:
                    raise AssetNotFound("asset is not visible in the current tenant")
                existing = uow.revisions.get(asset_revision_id)
                if existing is not None:
                    state = uow.revision_states.get(asset_revision_id)
                    if state is None:
                        raise AssetPersistenceFailed(
                            "registered revision is missing revision state"
                        )
                    _assert_compatible_revision(
                        existing,
                        asset_id=asset_id,
                        resource_type=locked.resource_type,
                        prepared=prepared,
                        media_type=media_type,
                    )
                    return RegisteredRevision(revision=existing, state=state)
                if locked.lifecycle is AssetLifecycle.DELETED:
                    raise AssetTransitionRejected(
                        "a deleted asset must not receive a new revision"
                    )
                next_number = AssetRevisionNumber(
                    uow.revisions.max_revision_number(asset_id) + 1
                )
                revision = AssetRevision(
                    tenant_id=tenant_id,
                    asset_id=asset_id,
                    asset_revision_id=asset_revision_id,
                    revision_number=next_number,
                    resource_type=locked.resource_type,
                    storage_key=prepared.storage_key,
                    media_type=media_type,
                    byte_size=prepared.byte_size,
                    sha256=prepared.sha256,
                    created_at=now,
                    created_by_principal_id=principal_id,
                )
                state = AssetRevisionState(
                    tenant_id=tenant_id,
                    asset_id=asset_id,
                    asset_revision_id=asset_revision_id,
                    revision_number=next_number,
                    safety_state=AssetRevisionSafetyState.PENDING,
                    bytes_purged=False,
                    updated_at=now,
                )
                uow.revisions.insert(revision)
                uow.revision_states.insert(state)
                aggregate_n = int(locked.aggregate_revision)
                insert_required_asset_audit(
                    uow,
                    tenant_id=tenant_id,
                    action=SecurityAuditAction.ASSET_REVISION_REGISTER,
                    resource_type=locked.resource_type,
                    asset_id=asset_id,
                    resource_revision_before=aggregate_n,
                    resource_revision_after=aggregate_n,
                    mutation_event_context=mutation_event_context,
                    audit_provenance=audit_provenance,
                    occurred_at=now,
                    revision_number=next_number,
                )
                uow.commit()
                return RegisteredRevision(revision=revision, state=state)
        except AssetIdentityConflict:
            return self._recover_revision(
                tenant_id,
                asset_id=asset_id,
                asset_revision_id=asset_revision_id,
                prepared=prepared,
                media_type=media_type,
            )

    def activate_revision(
        self,
        *,
        tenant_id: UUID,
        principal_id: UUID,
        asset_id: AssetId,
        resource_type: AssetResourceType | str,
        revision_number: AssetRevisionNumber,
        expected_aggregate_revision: AssetAggregateRevision,
        mutation_event_context: MutationEventContext,
        audit_provenance: AssetMutationAuditProvenance,
    ) -> Asset:
        require_foreign_uuid(tenant_id, label="tenant_id")
        require_foreign_uuid(principal_id, label="principal_id")
        parsed_type = parse_asset_resource_type(resource_type)
        self._authorize(
            tenant_id=tenant_id,
            principal_id=principal_id,
            capability=ASSET_REVISION_ACTIVATE,
            asset_id=asset_id,
        )
        candidate = self._read_activation_candidate(
            tenant_id,
            asset_id=asset_id,
            resource_type=parsed_type,
            revision_number=revision_number,
        )
        if candidate.asset.aggregate_revision != expected_aggregate_revision:
            raise AssetConflict("expected aggregate revision is stale")
        self._reject_unactivatable(candidate)
        try:
            observed = self._blob_store.inspect(
                storage_key=candidate.revision.storage_key
            )
        except BlobStoreUnavailableError:
            raise
        if observed is None:
            raise AssetActivationRejected(
                "bytes_missing", "physical bytes are absent"
            )
        if (
            observed.byte_size != candidate.revision.byte_size
            or observed.sha256 != candidate.revision.sha256
        ):
            raise AssetActivationRejected(
                "integrity_mismatch",
                "observed physical size or hash does not match revision facts",
            )
        with self._uow_factory(tenant_id) as uow:
            locked = uow.assets.get_for_update(asset_id)
            if locked is None or locked.resource_type != parsed_type:
                raise AssetNotFound("asset is not visible in the current tenant")
            if locked.aggregate_revision != expected_aggregate_revision:
                raise AssetConflict("expected aggregate revision is stale")
            revision = uow.revisions.get_by_asset_and_number(asset_id, revision_number)
            state = (
                None
                if revision is None
                else uow.revision_states.get(revision.asset_revision_id)
            )
            if revision is None or state is None:
                raise AssetConflict("activation candidate governing facts changed")
            if not _same_activation_facts(candidate, locked, revision, state):
                raise AssetConflict("activation candidate governing facts changed")
            if locked.lifecycle is AssetLifecycle.DELETED:
                raise AssetTransitionRejected("a deleted asset cannot be activated")
            updated = uow.assets.cas_current_revision(
                asset_id=asset_id,
                expected_revision=expected_aggregate_revision,
                current_revision=revision_number,
            )
            if updated is None:
                raise AssetConflict("expected aggregate revision is stale")
            insert_required_asset_audit(
                uow,
                tenant_id=tenant_id,
                action=SecurityAuditAction.ASSET_REVISION_ACTIVATE,
                resource_type=parsed_type,
                asset_id=asset_id,
                resource_revision_before=int(expected_aggregate_revision),
                resource_revision_after=int(expected_aggregate_revision) + 1,
                mutation_event_context=mutation_event_context,
                audit_provenance=audit_provenance,
                occurred_at=self._clock(),
                revision_number=revision_number,
            )
            uow.commit()
            return updated

    def withdraw_asset(
        self,
        *,
        tenant_id: UUID,
        principal_id: UUID,
        asset_id: AssetId,
        expected_aggregate_revision: AssetAggregateRevision,
        mutation_event_context: MutationEventContext,
        audit_provenance: AssetMutationAuditProvenance,
    ) -> Asset:
        self._authorize(
            tenant_id=tenant_id,
            principal_id=principal_id,
            capability=ASSET_LIFECYCLE_MANAGE,
            asset_id=asset_id,
        )
        return self._transition_lifecycle(
            tenant_id=tenant_id,
            principal_id=principal_id,
            asset_id=asset_id,
            expected_aggregate_revision=expected_aggregate_revision,
            to_lifecycle=AssetLifecycle.WITHDRAWN,
            mutation_event_context=mutation_event_context,
            audit_provenance=audit_provenance,
            audit_action=SecurityAuditAction.ASSET_LIFECYCLE_WITHDRAW,
        )

    def restore_asset(
        self,
        *,
        tenant_id: UUID,
        principal_id: UUID,
        asset_id: AssetId,
        expected_aggregate_revision: AssetAggregateRevision,
        mutation_event_context: MutationEventContext,
        audit_provenance: AssetMutationAuditProvenance,
    ) -> Asset:
        self._authorize(
            tenant_id=tenant_id,
            principal_id=principal_id,
            capability=ASSET_LIFECYCLE_MANAGE,
            asset_id=asset_id,
        )
        return self._transition_lifecycle(
            tenant_id=tenant_id,
            principal_id=principal_id,
            asset_id=asset_id,
            expected_aggregate_revision=expected_aggregate_revision,
            to_lifecycle=AssetLifecycle.ACTIVE,
            mutation_event_context=mutation_event_context,
            audit_provenance=audit_provenance,
            audit_action=SecurityAuditAction.ASSET_LIFECYCLE_RESTORE,
        )

    def delete_asset(
        self,
        *,
        tenant_id: UUID,
        principal_id: UUID,
        asset_id: AssetId,
        expected_aggregate_revision: AssetAggregateRevision,
        mutation_event_context: MutationEventContext,
        audit_provenance: AssetMutationAuditProvenance,
    ) -> Asset:
        self._authorize(
            tenant_id=tenant_id,
            principal_id=principal_id,
            capability=ASSET_LIFECYCLE_MANAGE,
            asset_id=asset_id,
        )
        return self._transition_lifecycle(
            tenant_id=tenant_id,
            principal_id=principal_id,
            asset_id=asset_id,
            expected_aggregate_revision=expected_aggregate_revision,
            to_lifecycle=AssetLifecycle.DELETED,
            mutation_event_context=mutation_event_context,
            audit_provenance=audit_provenance,
            audit_action=SecurityAuditAction.ASSET_LIFECYCLE_DELETE,
        )

    def quarantine_asset(
        self,
        *,
        tenant_id: UUID,
        principal_id: UUID,
        asset_id: AssetId,
        expected_aggregate_revision: AssetAggregateRevision,
        mutation_event_context: MutationEventContext,
        audit_provenance: AssetMutationAuditProvenance,
    ) -> Asset:
        self._authorize(
            tenant_id=tenant_id,
            principal_id=principal_id,
            capability=ASSET_QUARANTINE_MANAGE,
            asset_id=asset_id,
        )
        return self._transition_quarantine(
            tenant_id=tenant_id,
            principal_id=principal_id,
            asset_id=asset_id,
            expected_aggregate_revision=expected_aggregate_revision,
            to_quarantine=AssetQuarantineState.QUARANTINED,
            mutation_event_context=mutation_event_context,
            audit_provenance=audit_provenance,
            audit_action=SecurityAuditAction.ASSET_QUARANTINE_SET,
        )

    def clear_quarantine(
        self,
        *,
        tenant_id: UUID,
        principal_id: UUID,
        asset_id: AssetId,
        expected_aggregate_revision: AssetAggregateRevision,
        mutation_event_context: MutationEventContext,
        audit_provenance: AssetMutationAuditProvenance,
    ) -> Asset:
        self._authorize(
            tenant_id=tenant_id,
            principal_id=principal_id,
            capability=ASSET_QUARANTINE_MANAGE,
            asset_id=asset_id,
        )
        return self._transition_quarantine(
            tenant_id=tenant_id,
            principal_id=principal_id,
            asset_id=asset_id,
            expected_aggregate_revision=expected_aggregate_revision,
            to_quarantine=AssetQuarantineState.CLEAR,
            mutation_event_context=mutation_event_context,
            audit_provenance=audit_provenance,
            audit_action=SecurityAuditAction.ASSET_QUARANTINE_CLEAR,
        )

    def mark_safety_passed(
        self,
        *,
        tenant_id: UUID,
        principal_id: UUID,
        asset_id: AssetId,
        asset_revision_id: AssetRevisionId,
        expected_aggregate_revision: AssetAggregateRevision,
        mutation_event_context: MutationEventContext,
        audit_provenance: AssetMutationAuditProvenance,
    ) -> tuple[Asset, AssetRevisionState]:
        self._authorize(
            tenant_id=tenant_id,
            principal_id=principal_id,
            capability=ASSET_SAFETY_DECIDE,
            asset_id=asset_id,
        )
        return self._transition_safety(
            tenant_id=tenant_id,
            principal_id=principal_id,
            asset_id=asset_id,
            asset_revision_id=asset_revision_id,
            expected_aggregate_revision=expected_aggregate_revision,
            to_safety=AssetRevisionSafetyState.PASSED,
            mutation_event_context=mutation_event_context,
            audit_provenance=audit_provenance,
            audit_action=SecurityAuditAction.ASSET_SAFETY_PASS,
        )

    def mark_safety_failed(
        self,
        *,
        tenant_id: UUID,
        principal_id: UUID,
        asset_id: AssetId,
        asset_revision_id: AssetRevisionId,
        expected_aggregate_revision: AssetAggregateRevision,
        mutation_event_context: MutationEventContext,
        audit_provenance: AssetMutationAuditProvenance,
    ) -> tuple[Asset, AssetRevisionState]:
        self._authorize(
            tenant_id=tenant_id,
            principal_id=principal_id,
            capability=ASSET_SAFETY_DECIDE,
            asset_id=asset_id,
        )
        return self._transition_safety(
            tenant_id=tenant_id,
            principal_id=principal_id,
            asset_id=asset_id,
            asset_revision_id=asset_revision_id,
            expected_aggregate_revision=expected_aggregate_revision,
            to_safety=AssetRevisionSafetyState.FAILED,
            mutation_event_context=mutation_event_context,
            audit_provenance=audit_provenance,
            audit_action=SecurityAuditAction.ASSET_SAFETY_FAIL,
        )

    def _recover_create(
        self,
        tenant_id: UUID,
        asset_id: AssetId,
        resource_type: AssetResourceType,
    ) -> Asset:
        with self._uow_factory(tenant_id) as uow:
            existing = uow.assets.get(asset_id)
            if existing is None:
                raise AssetConflict("asset identity conflicts with an invisible row")
            _assert_compatible_create(existing, resource_type)
            return existing

    def _recover_revision(
        self,
        tenant_id: UUID,
        *,
        asset_id: AssetId,
        asset_revision_id: AssetRevisionId,
        prepared: PreparedBlob,
        media_type: str,
    ) -> RegisteredRevision:
        with self._uow_factory(tenant_id) as uow:
            existing = uow.revisions.get(asset_revision_id)
            if existing is None:
                raise AssetConflict(
                    "revision identity conflicts with an invisible row"
                )
            state = uow.revision_states.get(asset_revision_id)
            if state is None:
                raise AssetPersistenceFailed(
                    "registered revision is missing revision state"
                )
            asset = uow.assets.get(asset_id)
            resource_type = (
                asset.resource_type if asset is not None else existing.resource_type
            )
            _assert_compatible_revision(
                existing,
                asset_id=asset_id,
                resource_type=resource_type,
                prepared=prepared,
                media_type=media_type,
            )
            return RegisteredRevision(revision=existing, state=state)

    def _read_activation_candidate(
        self,
        tenant_id: UUID,
        *,
        asset_id: AssetId,
        resource_type: AssetResourceType,
        revision_number: AssetRevisionNumber,
    ) -> _ActivationCandidate:
        with self._uow_factory(tenant_id) as uow:
            asset = uow.assets.get(asset_id)
            if asset is None or asset.resource_type != resource_type:
                raise AssetNotFound("asset is not visible in the current tenant")
            if asset.lifecycle is AssetLifecycle.DELETED:
                raise AssetTransitionRejected("a deleted asset cannot be activated")
            revision = uow.revisions.get_by_asset_and_number(asset_id, revision_number)
            if revision is None or revision.resource_type != resource_type:
                raise AssetNotFound("revision is not visible for this asset")
            state = uow.revision_states.get(revision.asset_revision_id)
            if state is None:
                raise AssetPersistenceFailed("revision state is missing")
            return _ActivationCandidate(asset=asset, revision=revision, state=state)

    def _reject_unactivatable(self, candidate: _ActivationCandidate) -> None:
        if candidate.state.bytes_purged:
            raise AssetActivationRejected(
                "bytes_purged",
                "purged bytes are not usable for activation",
            )
        if candidate.state.safety_state is AssetRevisionSafetyState.PENDING:
            raise AssetActivationRejected(
                "safety_pending", "pending revisions cannot be newly activated"
            )
        if candidate.state.safety_state is AssetRevisionSafetyState.FAILED:
            raise AssetActivationRejected(
                "safety_failed", "failed revisions cannot be newly activated"
            )
        if candidate.state.safety_state is not AssetRevisionSafetyState.PASSED:
            raise AssetActivationRejected(
                "safety_invalid", "revision safety state is not passed"
            )

    def _transition_lifecycle(
        self,
        *,
        tenant_id: UUID,
        principal_id: UUID,
        asset_id: AssetId,
        expected_aggregate_revision: AssetAggregateRevision,
        to_lifecycle: AssetLifecycle,
        mutation_event_context: MutationEventContext,
        audit_provenance: AssetMutationAuditProvenance,
        audit_action: SecurityAuditAction,
    ) -> Asset:
        require_foreign_uuid(tenant_id, label="tenant_id")
        require_foreign_uuid(principal_id, label="principal_id")
        with self._uow_factory(tenant_id) as uow:
            locked = uow.assets.get_for_update(asset_id)
            if locked is None:
                raise AssetNotFound("asset is not visible in the current tenant")
            if locked.aggregate_revision != expected_aggregate_revision:
                raise AssetConflict("expected aggregate revision is stale")
            if (locked.lifecycle, to_lifecycle) not in _LIFECYCLE_ALLOWED:
                raise AssetTransitionRejected(
                    f"lifecycle {locked.lifecycle.value} -> {to_lifecycle.value} "
                    "is not allowed"
                )
            updated = uow.assets.cas_lifecycle(
                asset_id=asset_id,
                expected_revision=expected_aggregate_revision,
                from_lifecycle=locked.lifecycle,
                to_lifecycle=to_lifecycle,
            )
            if updated is None:
                raise AssetConflict("expected aggregate revision is stale")
            insert_required_asset_audit(
                uow,
                tenant_id=tenant_id,
                action=audit_action,
                resource_type=updated.resource_type,
                asset_id=asset_id,
                resource_revision_before=int(expected_aggregate_revision),
                resource_revision_after=int(expected_aggregate_revision) + 1,
                mutation_event_context=mutation_event_context,
                audit_provenance=audit_provenance,
                occurred_at=self._clock(),
            )
            uow.commit()
            return updated

    def _transition_quarantine(
        self,
        *,
        tenant_id: UUID,
        principal_id: UUID,
        asset_id: AssetId,
        expected_aggregate_revision: AssetAggregateRevision,
        to_quarantine: AssetQuarantineState,
        mutation_event_context: MutationEventContext,
        audit_provenance: AssetMutationAuditProvenance,
        audit_action: SecurityAuditAction,
    ) -> Asset:
        require_foreign_uuid(tenant_id, label="tenant_id")
        require_foreign_uuid(principal_id, label="principal_id")
        with self._uow_factory(tenant_id) as uow:
            locked = uow.assets.get_for_update(asset_id)
            if locked is None:
                raise AssetNotFound("asset is not visible in the current tenant")
            if locked.aggregate_revision != expected_aggregate_revision:
                raise AssetConflict("expected aggregate revision is stale")
            if locked.lifecycle is AssetLifecycle.DELETED:
                raise AssetTransitionRejected(
                    "quarantine cannot change after logical deletion"
                )
            if (locked.quarantine_state, to_quarantine) not in _QUARANTINE_ALLOWED:
                raise AssetTransitionRejected(
                    f"quarantine {locked.quarantine_state.value} -> "
                    f"{to_quarantine.value} is not allowed"
                )
            updated = uow.assets.cas_quarantine(
                asset_id=asset_id,
                expected_revision=expected_aggregate_revision,
                from_quarantine=locked.quarantine_state,
                to_quarantine=to_quarantine,
            )
            if updated is None:
                raise AssetConflict("expected aggregate revision is stale")
            insert_required_asset_audit(
                uow,
                tenant_id=tenant_id,
                action=audit_action,
                resource_type=updated.resource_type,
                asset_id=asset_id,
                resource_revision_before=int(expected_aggregate_revision),
                resource_revision_after=int(expected_aggregate_revision) + 1,
                mutation_event_context=mutation_event_context,
                audit_provenance=audit_provenance,
                occurred_at=self._clock(),
            )
            uow.commit()
            return updated

    def _transition_safety(
        self,
        *,
        tenant_id: UUID,
        principal_id: UUID,
        asset_id: AssetId,
        asset_revision_id: AssetRevisionId,
        expected_aggregate_revision: AssetAggregateRevision,
        to_safety: AssetRevisionSafetyState,
        mutation_event_context: MutationEventContext,
        audit_provenance: AssetMutationAuditProvenance,
        audit_action: SecurityAuditAction,
    ) -> tuple[Asset, AssetRevisionState]:
        require_foreign_uuid(tenant_id, label="tenant_id")
        require_foreign_uuid(principal_id, label="principal_id")
        with self._uow_factory(tenant_id) as uow:
            locked = uow.assets.get_for_update(asset_id)
            if locked is None:
                raise AssetNotFound("asset is not visible in the current tenant")
            if locked.aggregate_revision != expected_aggregate_revision:
                raise AssetConflict("expected aggregate revision is stale")
            revision = uow.revisions.get(asset_revision_id)
            if revision is None or revision.asset_id != asset_id:
                raise AssetNotFound("revision is not visible for this asset")
            state = uow.revision_states.get(asset_revision_id)
            if state is None:
                raise AssetPersistenceFailed("revision state is missing")
            if locked.lifecycle is AssetLifecycle.DELETED:
                if state.safety_state is not AssetRevisionSafetyState.PENDING:
                    raise AssetTransitionRejected(
                        "after deletion only a pending safety result may be finalized"
                    )
                if to_safety not in (
                    AssetRevisionSafetyState.PASSED,
                    AssetRevisionSafetyState.FAILED,
                ):
                    raise AssetTransitionRejected(
                        "after deletion only a pending safety result may be finalized"
                    )
            if (state.safety_state, to_safety) not in _SAFETY_ALLOWED:
                raise AssetTransitionRejected(
                    f"safety {state.safety_state.value} -> {to_safety.value} "
                    "is not allowed"
                )
            updated_state = uow.revision_states.cas_safety(
                asset_revision_id=asset_revision_id,
                from_safety=state.safety_state,
                to_safety=to_safety,
                updated_at=self._clock(),
            )
            if updated_state is None:
                raise AssetConflict("revision safety state changed concurrently")
            updated_asset = uow.assets.cas_increment_aggregate(
                asset_id=asset_id,
                expected_revision=expected_aggregate_revision,
            )
            if updated_asset is None:
                raise AssetConflict("expected aggregate revision is stale")
            insert_required_asset_audit(
                uow,
                tenant_id=tenant_id,
                action=audit_action,
                resource_type=revision.resource_type,
                asset_id=asset_id,
                resource_revision_before=int(expected_aggregate_revision),
                resource_revision_after=int(expected_aggregate_revision) + 1,
                mutation_event_context=mutation_event_context,
                audit_provenance=audit_provenance,
                occurred_at=self._clock(),
                revision_number=revision.revision_number,
            )
            uow.commit()
            return updated_asset, updated_state


def _assert_compatible_create(existing: Asset, resource_type: AssetResourceType) -> None:
    if existing.resource_type != resource_type:
        raise AssetConflict("asset creation identity conflicts with the existing row")


def _assert_compatible_revision(
    existing: AssetRevision,
    *,
    asset_id: AssetId,
    resource_type: AssetResourceType,
    prepared: PreparedBlob,
    media_type: str,
) -> None:
    if (
        existing.asset_id != asset_id
        or existing.resource_type != resource_type
        or existing.storage_key != prepared.storage_key
        or existing.media_type != media_type
        or existing.byte_size != prepared.byte_size
        or existing.sha256 != prepared.sha256
    ):
        raise AssetConflict(
            "revision registration identity conflicts with the existing row"
        )


def _same_activation_facts(
    candidate: _ActivationCandidate,
    locked: Asset,
    revision: AssetRevision,
    state: AssetRevisionState,
) -> bool:
    return (
        locked.aggregate_revision == candidate.asset.aggregate_revision
        and locked.lifecycle == candidate.asset.lifecycle
        and locked.quarantine_state == candidate.asset.quarantine_state
        and locked.current_revision == candidate.asset.current_revision
        and locked.resource_type == candidate.asset.resource_type
        and revision.asset_revision_id == candidate.revision.asset_revision_id
        and revision.revision_number == candidate.revision.revision_number
        and revision.storage_key == candidate.revision.storage_key
        and revision.byte_size == candidate.revision.byte_size
        and revision.sha256 == candidate.revision.sha256
        and revision.resource_type == candidate.revision.resource_type
        and state.safety_state == candidate.state.safety_state
        and state.bytes_purged == candidate.state.bytes_purged
        and state.safety_state is AssetRevisionSafetyState.PASSED
        and state.bytes_purged is False
    )

"""Asset current-use authority (PED-I10B4 / ADR-AIEOS-034).

Evaluates current usability from Asset SoR facts plus provider-neutral
BlobStore.inspect. SQLAlchemy stays in Asset infrastructure. This module
does not import SQLAlchemy, Content internals, or a production BlobStore.

principal_id is accepted for the frozen authority contract and is not used as
an Asset ACL, share, role, or capability decision.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from aieos.domains.asset.application.blob_store import BlobStore
from aieos.domains.asset.application.errors import (
    BlobStoreContractError,
    BlobStoreUnavailableError,
    InvalidBlobObjectInfoError,
)
from aieos.domains.asset.domain.resource_type import ASSET_RESOURCE_TYPES_V1
from aieos.domains.asset.domain.state import (
    AssetLifecycle,
    AssetQuarantineState,
    AssetRevisionSafetyState,
)
from aieos.platform.governance.errors import GovernanceUnavailableError
from aieos.platform.resources import ResourceRef
from aieos.platform.resources.asset_use import (
    AssetUseAssessment,
    AssetUseRejectionReason,
)

_POSITIVE_ATTEMPTS = 3
_GOVERNANCE_UNAVAILABLE = "governance unavailable"


@dataclass(frozen=True, slots=True)
class AssetIdentityFacts:
    """Tenant-visible Asset aggregate facts. Not authorization truth."""

    asset_id: UUID
    resource_type: str
    lifecycle: AssetLifecycle
    quarantine_state: AssetQuarantineState
    current_revision: int | None
    aggregate_revision: int


@dataclass(frozen=True, slots=True)
class RevisionFacts:
    """Immutable selected-revision byte facts."""

    asset_revision_id: UUID
    revision_number: int
    storage_key: str
    byte_size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class RevisionStateFacts:
    """Authoritative mutable revision-state facts."""

    safety_state: AssetRevisionSafetyState
    bytes_purged: bool


@dataclass(frozen=True, slots=True)
class GoverningSnapshot:
    """One authoritative read of identity + selected revision + revision-state."""

    identity: AssetIdentityFacts | None
    effective_revision_number: int | None
    revision: RevisionFacts | None
    revision_state: RevisionStateFacts | None

    def stability_key(self, *, unpinned: bool) -> tuple[object, ...]:
        identity = self.identity
        revision = self.revision
        state = self.revision_state
        if identity is None:
            return (None,)
        return (
            identity.lifecycle,
            identity.quarantine_state,
            identity.current_revision if unpinned else None,
            identity.aggregate_revision,
            None if revision is None else revision.asset_revision_id,
            None if revision is None else revision.revision_number,
            None if revision is None else revision.storage_key,
            None if revision is None else revision.byte_size,
            None if revision is None else revision.sha256,
            None if state is None else state.safety_state,
            None if state is None else state.bytes_purged,
        )


class AssetCurrentUseStore(Protocol):
    """Asset-owned current-use read port. Implementations live under infrastructure."""

    def load_governing_snapshot(
        self,
        *,
        tenant_id: UUID,
        asset_id: UUID,
        resource_type: str,
        pinned_revision: int | None,
    ) -> GoverningSnapshot: ...


class AssetCurrentUseAuthority:
    """Concrete AssetUseAuthority. Does not implement production BlobStore."""

    def __init__(
        self,
        store: AssetCurrentUseStore,
        blob_store: BlobStore,
        *,
        clock: Callable[[], datetime] | None = None,
        max_positive_attempts: int = _POSITIVE_ATTEMPTS,
    ) -> None:
        if max_positive_attempts < 1:
            raise ValueError("max_positive_attempts must be >= 1")
        self._store = store
        self._blob_store = blob_store
        self._clock = clock if clock is not None else (lambda: datetime.now(UTC))
        self._max_positive_attempts = max_positive_attempts

    def assess_use(
        self,
        *,
        tenant_id: UUID,
        principal_id: UUID,
        resource_ref: ResourceRef,
    ) -> AssetUseAssessment:
        _ = principal_id
        if resource_ref.resource_type not in ASSET_RESOURCE_TYPES_V1:
            return self._assessment(
                usable=False,
                reason_code=AssetUseRejectionReason.NOT_FOUND,
                authority_revision=None,
            )
        unpinned = resource_ref.resource_revision is None
        for _attempt in range(self._max_positive_attempts):
            snapshot = self._store.load_governing_snapshot(
                tenant_id=tenant_id,
                asset_id=resource_ref.resource_id,
                resource_type=resource_ref.resource_type,
                pinned_revision=resource_ref.resource_revision,
            )
            early = self._decide_without_blob(snapshot)
            if early is not None:
                return early
            assert snapshot.identity is not None
            assert snapshot.revision is not None
            assert snapshot.revision_state is not None
            first_key = snapshot.stability_key(unpinned=unpinned)
            observed = self._inspect(snapshot.revision.storage_key)
            if observed is None:
                return self._assessment(
                    usable=False,
                    reason_code=AssetUseRejectionReason.BYTES_MISSING,
                    authority_revision=snapshot.identity.aggregate_revision,
                )
            if (
                observed.byte_size != snapshot.revision.byte_size
                or observed.sha256 != snapshot.revision.sha256
            ):
                return self._assessment(
                    usable=False,
                    reason_code=AssetUseRejectionReason.INTEGRITY_MISMATCH,
                    authority_revision=snapshot.identity.aggregate_revision,
                )
            confirmed = self._store.load_governing_snapshot(
                tenant_id=tenant_id,
                asset_id=resource_ref.resource_id,
                resource_type=resource_ref.resource_type,
                pinned_revision=resource_ref.resource_revision,
            )
            if confirmed.stability_key(unpinned=unpinned) != first_key:
                continue
            if self._decide_without_blob(confirmed) is not None:
                continue
            return self._assessment(
                usable=True,
                reason_code=None,
                authority_revision=confirmed.identity.aggregate_revision
                if confirmed.identity is not None
                else snapshot.identity.aggregate_revision,
            )
        raise GovernanceUnavailableError(_GOVERNANCE_UNAVAILABLE)

    def _inspect(self, storage_key: str):
        try:
            observed = self._blob_store.inspect(storage_key=storage_key)
        except BlobStoreUnavailableError as exc:
            raise GovernanceUnavailableError(_GOVERNANCE_UNAVAILABLE) from exc
        except (InvalidBlobObjectInfoError, BlobStoreContractError) as exc:
            raise GovernanceUnavailableError(_GOVERNANCE_UNAVAILABLE) from exc
        if observed is None:
            return None
        if observed.storage_key != storage_key:
            raise GovernanceUnavailableError(_GOVERNANCE_UNAVAILABLE)
        return observed

    def _decide_without_blob(
        self, snapshot: GoverningSnapshot
    ) -> AssetUseAssessment | None:
        identity = snapshot.identity
        if identity is None:
            return self._assessment(
                usable=False,
                reason_code=AssetUseRejectionReason.NOT_FOUND,
                authority_revision=None,
            )
        if identity.lifecycle == AssetLifecycle.DELETED:
            return self._assessment(
                usable=False,
                reason_code=AssetUseRejectionReason.DELETED,
                authority_revision=identity.aggregate_revision,
            )
        if identity.lifecycle == AssetLifecycle.WITHDRAWN:
            return self._assessment(
                usable=False,
                reason_code=AssetUseRejectionReason.WITHDRAWN,
                authority_revision=identity.aggregate_revision,
            )
        if identity.quarantine_state == AssetQuarantineState.QUARANTINED:
            return self._assessment(
                usable=False,
                reason_code=AssetUseRejectionReason.QUARANTINED,
                authority_revision=identity.aggregate_revision,
            )
        if snapshot.effective_revision_number is None:
            return self._assessment(
                usable=False,
                reason_code=AssetUseRejectionReason.REVISION_NOT_FOUND,
                authority_revision=identity.aggregate_revision,
            )
        if snapshot.revision is None:
            return self._assessment(
                usable=False,
                reason_code=AssetUseRejectionReason.REVISION_NOT_FOUND,
                authority_revision=identity.aggregate_revision,
            )
        if snapshot.revision_state is None:
            raise GovernanceUnavailableError(_GOVERNANCE_UNAVAILABLE)
        if snapshot.revision_state.safety_state == AssetRevisionSafetyState.FAILED:
            return self._assessment(
                usable=False,
                reason_code=AssetUseRejectionReason.SAFETY_FAILED,
                authority_revision=identity.aggregate_revision,
            )
        if snapshot.revision_state.safety_state == AssetRevisionSafetyState.PENDING:
            return self._assessment(
                usable=False,
                reason_code=AssetUseRejectionReason.SAFETY_PENDING,
                authority_revision=identity.aggregate_revision,
            )
        if snapshot.revision_state.bytes_purged:
            return self._assessment(
                usable=False,
                reason_code=AssetUseRejectionReason.BYTES_PURGED,
                authority_revision=identity.aggregate_revision,
            )
        return None

    def _assessment(
        self,
        *,
        usable: bool,
        reason_code: AssetUseRejectionReason | None,
        authority_revision: int | None,
    ) -> AssetUseAssessment:
        return AssetUseAssessment(
            usable=usable,
            reason_code=reason_code,
            authority_revision=authority_revision,
            observed_at=self._clock(),
        )

"""Asset write persistence ports (PED-I10B5). Infrastructure types are not part of this contract."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from aieos.domains.asset.domain.asset import Asset
from aieos.domains.asset.domain.identities import (
    AssetAggregateRevision,
    AssetId,
    AssetRevisionId,
    AssetRevisionNumber,
)
from aieos.domains.asset.domain.revision import AssetRevision, AssetRevisionState
from aieos.domains.asset.domain.state import (
    AssetLifecycle,
    AssetQuarantineState,
    AssetRevisionSafetyState,
)


class AssetWriteRepository(Protocol):
    """INSERT/READ/CAS persistence for the Asset aggregate."""

    def get(self, asset_id: AssetId) -> Asset | None: ...

    def get_for_update(self, asset_id: AssetId) -> Asset | None: ...

    def insert(self, asset: Asset) -> None: ...

    def cas_lifecycle(
        self,
        *,
        asset_id: AssetId,
        expected_revision: AssetAggregateRevision,
        from_lifecycle: AssetLifecycle,
        to_lifecycle: AssetLifecycle,
    ) -> Asset | None: ...

    def cas_quarantine(
        self,
        *,
        asset_id: AssetId,
        expected_revision: AssetAggregateRevision,
        from_quarantine: AssetQuarantineState,
        to_quarantine: AssetQuarantineState,
    ) -> Asset | None: ...

    def cas_current_revision(
        self,
        *,
        asset_id: AssetId,
        expected_revision: AssetAggregateRevision,
        current_revision: AssetRevisionNumber,
    ) -> Asset | None: ...

    def cas_increment_aggregate(
        self,
        *,
        asset_id: AssetId,
        expected_revision: AssetAggregateRevision,
    ) -> Asset | None: ...


class AssetRevisionWriteRepository(Protocol):
    """INSERT/READ persistence for immutable AssetRevision rows."""

    def get(self, asset_revision_id: AssetRevisionId) -> AssetRevision | None: ...

    def get_by_asset_and_number(
        self, asset_id: AssetId, revision_number: AssetRevisionNumber
    ) -> AssetRevision | None: ...

    def max_revision_number(self, asset_id: AssetId) -> int: ...

    def insert(self, revision: AssetRevision) -> None: ...


class AssetRevisionStateWriteRepository(Protocol):
    """INSERT/CAS persistence for AssetRevisionState rows. Never sets bytes_purged true."""

    def get(self, asset_revision_id: AssetRevisionId) -> AssetRevisionState | None: ...

    def insert(self, state: AssetRevisionState) -> None: ...

    def cas_safety(
        self,
        *,
        asset_revision_id: AssetRevisionId,
        from_safety: AssetRevisionSafetyState,
        to_safety: AssetRevisionSafetyState,
        updated_at: datetime,
    ) -> AssetRevisionState | None: ...


class AssetUnitOfWork(Protocol):
    """One Asset write transaction. Repositories do not commit or rollback."""

    assets: AssetWriteRepository
    revisions: AssetRevisionWriteRepository
    revision_states: AssetRevisionStateWriteRepository

    def __enter__(self) -> AssetUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object,
    ) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


class AssetUnitOfWorkFactory(Protocol):
    def __call__(self, execution_tenant_id: UUID) -> AssetUnitOfWork: ...

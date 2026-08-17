"""Test-only in-memory Asset write UoW. Not a production adapter."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from uuid import UUID

from aieos.domains.asset.application.mutation_errors import AssetIdentityConflict
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


@dataclass
class MemoryAssetCatalog:
    assets: dict[tuple[UUID, UUID], Asset]
    revisions: dict[UUID, AssetRevision]
    states: dict[UUID, AssetRevisionState]
    commits: int = 0
    rollbacks: int = 0

    def __init__(self) -> None:
        self.assets = {}
        self.revisions = {}
        self.states = {}
        self.commits = 0
        self.rollbacks = 0


class InMemoryAssetWriteRepository:
    def __init__(self, catalog: MemoryAssetCatalog, tenant_id: UUID) -> None:
        self._catalog = catalog
        self._tenant_id = tenant_id

    def get(self, asset_id: AssetId) -> Asset | None:
        return self._catalog.assets.get((self._tenant_id, asset_id.value))

    def get_for_update(self, asset_id: AssetId) -> Asset | None:
        return self.get(asset_id)

    def insert(self, asset: Asset) -> None:
        for (_, existing_id), _row in self._catalog.assets.items():
            if existing_id == asset.asset_id.value:
                raise AssetIdentityConflict("asset identity already exists")
        self._catalog.assets[(self._tenant_id, asset.asset_id.value)] = asset

    def cas_lifecycle(
        self,
        *,
        asset_id: AssetId,
        expected_revision: AssetAggregateRevision,
        from_lifecycle: AssetLifecycle,
        to_lifecycle: AssetLifecycle,
    ) -> Asset | None:
        current = self.get(asset_id)
        if (
            current is None
            or current.aggregate_revision != expected_revision
            or current.lifecycle != from_lifecycle
        ):
            return None
        updated = replace(
            current,
            lifecycle=to_lifecycle,
            aggregate_revision=AssetAggregateRevision(int(current.aggregate_revision) + 1),
        )
        self._catalog.assets[(self._tenant_id, asset_id.value)] = updated
        return updated

    def cas_quarantine(
        self,
        *,
        asset_id: AssetId,
        expected_revision: AssetAggregateRevision,
        from_quarantine: AssetQuarantineState,
        to_quarantine: AssetQuarantineState,
    ) -> Asset | None:
        current = self.get(asset_id)
        if (
            current is None
            or current.aggregate_revision != expected_revision
            or current.quarantine_state != from_quarantine
        ):
            return None
        updated = replace(
            current,
            quarantine_state=to_quarantine,
            aggregate_revision=AssetAggregateRevision(int(current.aggregate_revision) + 1),
        )
        self._catalog.assets[(self._tenant_id, asset_id.value)] = updated
        return updated

    def cas_current_revision(
        self,
        *,
        asset_id: AssetId,
        expected_revision: AssetAggregateRevision,
        current_revision: AssetRevisionNumber,
    ) -> Asset | None:
        current = self.get(asset_id)
        if current is None or current.aggregate_revision != expected_revision:
            return None
        updated = replace(
            current,
            current_revision=current_revision,
            aggregate_revision=AssetAggregateRevision(int(current.aggregate_revision) + 1),
        )
        self._catalog.assets[(self._tenant_id, asset_id.value)] = updated
        return updated

    def cas_increment_aggregate(
        self,
        *,
        asset_id: AssetId,
        expected_revision: AssetAggregateRevision,
    ) -> Asset | None:
        current = self.get(asset_id)
        if current is None or current.aggregate_revision != expected_revision:
            return None
        updated = replace(
            current,
            aggregate_revision=AssetAggregateRevision(int(current.aggregate_revision) + 1),
        )
        self._catalog.assets[(self._tenant_id, asset_id.value)] = updated
        return updated


class InMemoryAssetRevisionWriteRepository:
    def __init__(self, catalog: MemoryAssetCatalog, tenant_id: UUID) -> None:
        self._catalog = catalog
        self._tenant_id = tenant_id

    def get(self, asset_revision_id: AssetRevisionId) -> AssetRevision | None:
        revision = self._catalog.revisions.get(asset_revision_id.value)
        if revision is None or revision.tenant_id != self._tenant_id:
            return None
        return revision

    def get_by_asset_and_number(
        self, asset_id: AssetId, revision_number: AssetRevisionNumber
    ) -> AssetRevision | None:
        for revision in self._catalog.revisions.values():
            if (
                revision.tenant_id == self._tenant_id
                and revision.asset_id == asset_id
                and revision.revision_number == revision_number
            ):
                return revision
        return None

    def max_revision_number(self, asset_id: AssetId) -> int:
        numbers = [
            int(revision.revision_number)
            for revision in self._catalog.revisions.values()
            if revision.tenant_id == self._tenant_id and revision.asset_id == asset_id
        ]
        return max(numbers) if numbers else 0

    def insert(self, revision: AssetRevision) -> None:
        if revision.asset_revision_id.value in self._catalog.revisions:
            raise AssetIdentityConflict("asset revision identity already exists")
        self._catalog.revisions[revision.asset_revision_id.value] = revision


class InMemoryAssetRevisionStateWriteRepository:
    def __init__(self, catalog: MemoryAssetCatalog, tenant_id: UUID) -> None:
        self._catalog = catalog
        self._tenant_id = tenant_id

    def get(self, asset_revision_id: AssetRevisionId) -> AssetRevisionState | None:
        state = self._catalog.states.get(asset_revision_id.value)
        if state is None or state.tenant_id != self._tenant_id:
            return None
        return state

    def insert(self, state: AssetRevisionState) -> None:
        if state.asset_revision_id.value in self._catalog.states:
            raise AssetIdentityConflict("asset revision state identity already exists")
        self._catalog.states[state.asset_revision_id.value] = replace(
            state, bytes_purged=False
        )

    def cas_safety(
        self,
        *,
        asset_revision_id: AssetRevisionId,
        from_safety: AssetRevisionSafetyState,
        to_safety: AssetRevisionSafetyState,
        updated_at: datetime,
    ) -> AssetRevisionState | None:
        current = self.get(asset_revision_id)
        if current is None or current.safety_state != from_safety:
            return None
        updated = replace(
            current, safety_state=to_safety, updated_at=updated_at
        )
        self._catalog.states[asset_revision_id.value] = updated
        return updated


class InMemoryAssetUnitOfWork:
    def __init__(self, catalog: MemoryAssetCatalog, tenant_id: UUID) -> None:
        self._catalog = catalog
        self._tenant_id = tenant_id
        self._committed = False
        self._snapshot: tuple[dict, dict, dict] | None = None
        self.assets: InMemoryAssetWriteRepository
        self.revisions: InMemoryAssetRevisionWriteRepository
        self.revision_states: InMemoryAssetRevisionStateWriteRepository

    def __enter__(self) -> InMemoryAssetUnitOfWork:
        self._committed = False
        self._snapshot = (
            dict(self._catalog.assets),
            dict(self._catalog.revisions),
            dict(self._catalog.states),
        )
        self.assets = InMemoryAssetWriteRepository(self._catalog, self._tenant_id)
        self.revisions = InMemoryAssetRevisionWriteRepository(
            self._catalog, self._tenant_id
        )
        self.revision_states = InMemoryAssetRevisionStateWriteRepository(
            self._catalog, self._tenant_id
        )
        return self

    def commit(self) -> None:
        self._committed = True
        self._catalog.commits += 1

    def rollback(self) -> None:
        self._restore()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object,
    ) -> None:
        if not self._committed:
            self._restore()

    def _restore(self) -> None:
        if self._snapshot is None:
            return
        self._catalog.assets.clear()
        self._catalog.assets.update(self._snapshot[0])
        self._catalog.revisions.clear()
        self._catalog.revisions.update(self._snapshot[1])
        self._catalog.states.clear()
        self._catalog.states.update(self._snapshot[2])
        self._catalog.rollbacks += 1
        self._snapshot = None


class InMemoryAssetUnitOfWorkFactory:
    def __init__(self, catalog: MemoryAssetCatalog | None = None) -> None:
        self.catalog = catalog if catalog is not None else MemoryAssetCatalog()

    def __call__(self, execution_tenant_id: UUID) -> InMemoryAssetUnitOfWork:
        return InMemoryAssetUnitOfWork(self.catalog, execution_tenant_id)

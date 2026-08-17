"""SQLAlchemy Core write repositories for Asset mutations (PED-I10B5).

Repositories never commit or rollback. The Asset Unit of Work owns the
transaction. bytes_purged is inserted false and never updated to true.
deletion_evidence is never written.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.engine import Connection

from aieos.domains.asset.application.mutation_errors import AssetPersistenceFailed
from aieos.domains.asset.domain.asset import Asset
from aieos.domains.asset.domain.identities import (
    AssetAggregateRevision,
    AssetId,
    AssetRevisionId,
    AssetRevisionNumber,
)
from aieos.domains.asset.domain.resource_type import parse_asset_resource_type
from aieos.domains.asset.domain.revision import AssetRevision, AssetRevisionState
from aieos.domains.asset.domain.state import (
    AssetLifecycle,
    AssetQuarantineState,
    AssetRevisionSafetyState,
    parse_asset_lifecycle,
    parse_asset_quarantine_state,
    parse_asset_revision_safety_state,
)
from aieos.domains.asset.infrastructure.persistence.errors import (
    reraise_as_application_error,
)
from aieos.domains.asset.infrastructure.persistence.models import (
    asset_revision_states_table,
    asset_revisions_table,
    assets_table,
)


def _as_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise AssetPersistenceFailed("asset persistence operation failed")
    return value


def _asset_from_mapping(row: object) -> Asset:
    mapping = dict(row)  # type: ignore[arg-type]
    current = mapping["current_revision"]
    try:
        return Asset(
            tenant_id=mapping["tenant_id"],
            asset_id=AssetId(mapping["asset_id"]),
            resource_type=parse_asset_resource_type(mapping["resource_type"]),
            lifecycle=parse_asset_lifecycle(mapping["lifecycle"]),
            quarantine_state=parse_asset_quarantine_state(mapping["quarantine_state"]),
            current_revision=(
                None if current is None else AssetRevisionNumber(_as_int(current))
            ),
            aggregate_revision=AssetAggregateRevision(
                _as_int(mapping["aggregate_revision"])
            ),
            created_at=mapping["created_at"],
            created_by_principal_id=mapping["created_by_principal_id"],
        )
    except AssetPersistenceFailed:
        raise
    except Exception as exc:
        raise AssetPersistenceFailed("asset persistence operation failed") from exc


def _revision_from_mapping(row: object) -> AssetRevision:
    mapping = dict(row)  # type: ignore[arg-type]
    try:
        return AssetRevision(
            tenant_id=mapping["tenant_id"],
            asset_id=AssetId(mapping["asset_id"]),
            asset_revision_id=AssetRevisionId(mapping["asset_revision_id"]),
            revision_number=AssetRevisionNumber(_as_int(mapping["revision_number"])),
            resource_type=parse_asset_resource_type(mapping["resource_type"]),
            storage_key=mapping["storage_key"],
            media_type=mapping["media_type"],
            byte_size=_as_int(mapping["byte_size"]),
            sha256=mapping["sha256"],
            created_at=mapping["created_at"],
            created_by_principal_id=mapping["created_by_principal_id"],
        )
    except AssetPersistenceFailed:
        raise
    except Exception as exc:
        raise AssetPersistenceFailed("asset persistence operation failed") from exc


def _state_from_mapping(row: object) -> AssetRevisionState:
    mapping = dict(row)  # type: ignore[arg-type]
    purged = mapping["bytes_purged"]
    if not isinstance(purged, bool):
        raise AssetPersistenceFailed("asset persistence operation failed")
    try:
        return AssetRevisionState(
            tenant_id=mapping["tenant_id"],
            asset_id=AssetId(mapping["asset_id"]),
            asset_revision_id=AssetRevisionId(mapping["asset_revision_id"]),
            revision_number=AssetRevisionNumber(_as_int(mapping["revision_number"])),
            safety_state=parse_asset_revision_safety_state(mapping["safety_state"]),
            bytes_purged=purged,
            updated_at=mapping["updated_at"],
        )
    except AssetPersistenceFailed:
        raise
    except Exception as exc:
        raise AssetPersistenceFailed("asset persistence operation failed") from exc


class SqlAlchemyAssetWriteRepository:
    def __init__(self, connection: Connection, execution_tenant_id: UUID) -> None:
        self._connection = connection
        self._execution_tenant_id = execution_tenant_id

    def get(self, asset_id: AssetId) -> Asset | None:
        stmt = select(assets_table).where(
            assets_table.c.tenant_id == self._execution_tenant_id,
            assets_table.c.asset_id == asset_id.value,
        )
        try:
            row = self._connection.execute(stmt).mappings().first()
        except Exception as exc:
            reraise_as_application_error(exc)
        if row is None:
            return None
        return _asset_from_mapping(row)

    def get_for_update(self, asset_id: AssetId) -> Asset | None:
        stmt = (
            select(assets_table)
            .where(
                assets_table.c.tenant_id == self._execution_tenant_id,
                assets_table.c.asset_id == asset_id.value,
            )
            .with_for_update()
        )
        try:
            row = self._connection.execute(stmt).mappings().first()
        except Exception as exc:
            reraise_as_application_error(exc)
        if row is None:
            return None
        return _asset_from_mapping(row)

    def insert(self, asset: Asset) -> None:
        stmt = assets_table.insert().values(
            tenant_id=asset.tenant_id,
            asset_id=asset.asset_id.value,
            resource_type=asset.resource_type.value,
            lifecycle=asset.lifecycle.value,
            quarantine_state=asset.quarantine_state.value,
            current_revision=None,
            aggregate_revision=int(asset.aggregate_revision),
            created_at=asset.created_at,
            created_by_principal_id=asset.created_by_principal_id,
        )
        try:
            self._connection.execute(stmt)
        except Exception as exc:
            reraise_as_application_error(exc)

    def cas_lifecycle(
        self,
        *,
        asset_id: AssetId,
        expected_revision: AssetAggregateRevision,
        from_lifecycle: AssetLifecycle,
        to_lifecycle: AssetLifecycle,
    ) -> Asset | None:
        return self._cas_update(
            asset_id=asset_id,
            expected_revision=expected_revision,
            extra_where={assets_table.c.lifecycle: from_lifecycle.value},
            values={"lifecycle": to_lifecycle.value},
        )

    def cas_quarantine(
        self,
        *,
        asset_id: AssetId,
        expected_revision: AssetAggregateRevision,
        from_quarantine: AssetQuarantineState,
        to_quarantine: AssetQuarantineState,
    ) -> Asset | None:
        return self._cas_update(
            asset_id=asset_id,
            expected_revision=expected_revision,
            extra_where={assets_table.c.quarantine_state: from_quarantine.value},
            values={"quarantine_state": to_quarantine.value},
        )

    def cas_current_revision(
        self,
        *,
        asset_id: AssetId,
        expected_revision: AssetAggregateRevision,
        current_revision: AssetRevisionNumber,
    ) -> Asset | None:
        return self._cas_update(
            asset_id=asset_id,
            expected_revision=expected_revision,
            extra_where={},
            values={"current_revision": int(current_revision)},
        )

    def cas_increment_aggregate(
        self,
        *,
        asset_id: AssetId,
        expected_revision: AssetAggregateRevision,
    ) -> Asset | None:
        return self._cas_update(
            asset_id=asset_id,
            expected_revision=expected_revision,
            extra_where={},
            values={},
        )

    def _cas_update(
        self,
        *,
        asset_id: AssetId,
        expected_revision: AssetAggregateRevision,
        extra_where: dict,
        values: dict,
    ) -> Asset | None:
        conditions = [
            assets_table.c.tenant_id == self._execution_tenant_id,
            assets_table.c.asset_id == asset_id.value,
            assets_table.c.aggregate_revision == int(expected_revision),
        ]
        for column, expected in extra_where.items():
            conditions.append(column == expected)
        assigned = {
            **values,
            "aggregate_revision": assets_table.c.aggregate_revision + 1,
        }
        stmt = (
            update(assets_table)
            .where(*conditions)
            .values(**assigned)
            .returning(assets_table)
        )
        try:
            row = self._connection.execute(stmt).mappings().first()
        except Exception as exc:
            reraise_as_application_error(exc)
        if row is None:
            return None
        return _asset_from_mapping(row)


class SqlAlchemyAssetRevisionWriteRepository:
    def __init__(self, connection: Connection, execution_tenant_id: UUID) -> None:
        self._connection = connection
        self._execution_tenant_id = execution_tenant_id

    def get(self, asset_revision_id: AssetRevisionId) -> AssetRevision | None:
        stmt = select(asset_revisions_table).where(
            asset_revisions_table.c.tenant_id == self._execution_tenant_id,
            asset_revisions_table.c.asset_revision_id == asset_revision_id.value,
        )
        try:
            row = self._connection.execute(stmt).mappings().first()
        except Exception as exc:
            reraise_as_application_error(exc)
        if row is None:
            return None
        return _revision_from_mapping(row)

    def get_by_asset_and_number(
        self, asset_id: AssetId, revision_number: AssetRevisionNumber
    ) -> AssetRevision | None:
        stmt = select(asset_revisions_table).where(
            asset_revisions_table.c.tenant_id == self._execution_tenant_id,
            asset_revisions_table.c.asset_id == asset_id.value,
            asset_revisions_table.c.revision_number == int(revision_number),
        )
        try:
            row = self._connection.execute(stmt).mappings().first()
        except Exception as exc:
            reraise_as_application_error(exc)
        if row is None:
            return None
        return _revision_from_mapping(row)

    def max_revision_number(self, asset_id: AssetId) -> int:
        stmt = select(func.max(asset_revisions_table.c.revision_number)).where(
            asset_revisions_table.c.tenant_id == self._execution_tenant_id,
            asset_revisions_table.c.asset_id == asset_id.value,
        )
        try:
            value = self._connection.execute(stmt).scalar()
        except Exception as exc:
            reraise_as_application_error(exc)
        if value is None:
            return 0
        return _as_int(value)

    def insert(self, revision: AssetRevision) -> None:
        stmt = asset_revisions_table.insert().values(
            asset_revision_id=revision.asset_revision_id.value,
            tenant_id=revision.tenant_id,
            asset_id=revision.asset_id.value,
            revision_number=int(revision.revision_number),
            resource_type=revision.resource_type.value,
            storage_key=revision.storage_key,
            media_type=revision.media_type,
            byte_size=revision.byte_size,
            sha256=revision.sha256,
            created_at=revision.created_at,
            created_by_principal_id=revision.created_by_principal_id,
        )
        try:
            self._connection.execute(stmt)
        except Exception as exc:
            reraise_as_application_error(exc)


class SqlAlchemyAssetRevisionStateWriteRepository:
    def __init__(self, connection: Connection, execution_tenant_id: UUID) -> None:
        self._connection = connection
        self._execution_tenant_id = execution_tenant_id

    def get(self, asset_revision_id: AssetRevisionId) -> AssetRevisionState | None:
        stmt = select(asset_revision_states_table).where(
            asset_revision_states_table.c.tenant_id == self._execution_tenant_id,
            asset_revision_states_table.c.asset_revision_id == asset_revision_id.value,
        )
        try:
            row = self._connection.execute(stmt).mappings().first()
        except Exception as exc:
            reraise_as_application_error(exc)
        if row is None:
            return None
        return _state_from_mapping(row)

    def insert(self, state: AssetRevisionState) -> None:
        stmt = asset_revision_states_table.insert().values(
            asset_revision_id=state.asset_revision_id.value,
            tenant_id=state.tenant_id,
            asset_id=state.asset_id.value,
            revision_number=int(state.revision_number),
            safety_state=state.safety_state.value,
            bytes_purged=False,
            updated_at=state.updated_at,
        )
        try:
            self._connection.execute(stmt)
        except Exception as exc:
            reraise_as_application_error(exc)

    def cas_safety(
        self,
        *,
        asset_revision_id: AssetRevisionId,
        from_safety: AssetRevisionSafetyState,
        to_safety: AssetRevisionSafetyState,
        updated_at: datetime,
    ) -> AssetRevisionState | None:
        stmt = (
            update(asset_revision_states_table)
            .where(
                asset_revision_states_table.c.tenant_id == self._execution_tenant_id,
                asset_revision_states_table.c.asset_revision_id
                == asset_revision_id.value,
                asset_revision_states_table.c.safety_state == from_safety.value,
            )
            .values(safety_state=to_safety.value, updated_at=updated_at)
            .returning(asset_revision_states_table)
        )
        try:
            row = self._connection.execute(stmt).mappings().first()
        except Exception as exc:
            reraise_as_application_error(exc)
        if row is None:
            return None
        return _state_from_mapping(row)

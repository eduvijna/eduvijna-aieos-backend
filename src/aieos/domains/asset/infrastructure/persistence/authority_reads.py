"""Asset-owned current-use reads over the PED-I10B2 tables.

SQLAlchemy stays here. No bypass of row-level security, no cross-tenant
existence probe, and no Python post-filter of all-tenant rows. Invisible rows
are indistinguishable from absence.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError

from aieos.domains.asset.application.use_authority import (
    AssetIdentityFacts,
    GoverningSnapshot,
    RevisionFacts,
    RevisionStateFacts,
)
from aieos.domains.asset.domain.errors import (
    InvalidAssetResourceTypeError,
    InvalidAssetStateError,
)
from aieos.domains.asset.domain.resource_type import parse_asset_resource_type
from aieos.domains.asset.domain.state import (
    parse_asset_lifecycle,
    parse_asset_quarantine_state,
    parse_asset_revision_safety_state,
)
from aieos.domains.asset.infrastructure.persistence.models import (
    asset_revision_states_table,
    asset_revisions_table,
    assets_table,
)
from aieos.domains.asset.infrastructure.persistence.session import asset_authority_read
from aieos.platform.governance.errors import GovernanceUnavailableError

_GOVERNANCE_UNAVAILABLE = "governance unavailable"


def _as_int(value: object, *, allow_none: bool = False) -> int | None:
    if value is None:
        if allow_none:
            return None
        raise GovernanceUnavailableError(_GOVERNANCE_UNAVAILABLE)
    if isinstance(value, bool) or not isinstance(value, int):
        raise GovernanceUnavailableError(_GOVERNANCE_UNAVAILABLE)
    return value


def _require_int(value: object) -> int:
    parsed = _as_int(value)
    if parsed is None:
        raise GovernanceUnavailableError(_GOVERNANCE_UNAVAILABLE)
    return parsed


def fetch_typed_asset(
    conn: Connection,
    *,
    tenant_id: UUID,
    asset_id: UUID,
    resource_type: str,
):
    """Load the exact typed Asset visible in this connection's RLS scope."""
    stmt = select(assets_table).where(
        assets_table.c.tenant_id == tenant_id,
        assets_table.c.asset_id == asset_id,
        assets_table.c.resource_type == resource_type,
    )
    try:
        return conn.execute(stmt).mappings().first()
    except SQLAlchemyError as exc:
        raise GovernanceUnavailableError(_GOVERNANCE_UNAVAILABLE) from exc


def fetch_revision(
    conn: Connection,
    *,
    tenant_id: UUID,
    asset_id: UUID,
    revision_number: int,
):
    stmt = select(asset_revisions_table).where(
        asset_revisions_table.c.tenant_id == tenant_id,
        asset_revisions_table.c.asset_id == asset_id,
        asset_revisions_table.c.revision_number == revision_number,
    )
    try:
        return conn.execute(stmt).mappings().first()
    except SQLAlchemyError as exc:
        raise GovernanceUnavailableError(_GOVERNANCE_UNAVAILABLE) from exc


def fetch_revision_state(
    conn: Connection,
    *,
    tenant_id: UUID,
    asset_revision_id: UUID,
):
    stmt = select(asset_revision_states_table).where(
        asset_revision_states_table.c.tenant_id == tenant_id,
        asset_revision_states_table.c.asset_revision_id == asset_revision_id,
    )
    try:
        return conn.execute(stmt).mappings().first()
    except SQLAlchemyError as exc:
        raise GovernanceUnavailableError(_GOVERNANCE_UNAVAILABLE) from exc


class PostgresAssetCurrentUseStore:
    """RLS-enforced PostgreSQL adapter for Asset current-use reads."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def load_governing_snapshot(
        self,
        *,
        tenant_id: UUID,
        asset_id: UUID,
        resource_type: str,
        pinned_revision: int | None,
    ) -> GoverningSnapshot:
        try:
            with asset_authority_read(
                self._engine, query_tenant_id=tenant_id
            ) as conn:
                return self._load(
                    conn,
                    tenant_id=tenant_id,
                    asset_id=asset_id,
                    resource_type=resource_type,
                    pinned_revision=pinned_revision,
                )
        except (
            InvalidAssetStateError,
            InvalidAssetResourceTypeError,
        ) as exc:
            raise GovernanceUnavailableError(_GOVERNANCE_UNAVAILABLE) from exc

    def _load(
        self,
        conn: Connection,
        *,
        tenant_id: UUID,
        asset_id: UUID,
        resource_type: str,
        pinned_revision: int | None,
    ) -> GoverningSnapshot:
        row = fetch_typed_asset(
            conn,
            tenant_id=tenant_id,
            asset_id=asset_id,
            resource_type=resource_type,
        )
        if row is None:
            return GoverningSnapshot(
                identity=None,
                effective_revision_number=None,
                revision=None,
                revision_state=None,
            )
        identity = AssetIdentityFacts(
            asset_id=row["asset_id"],
            resource_type=parse_asset_resource_type(row["resource_type"]).value,
            lifecycle=parse_asset_lifecycle(row["lifecycle"]),
            quarantine_state=parse_asset_quarantine_state(row["quarantine_state"]),
            current_revision=_as_int(row["current_revision"], allow_none=True),
            aggregate_revision=_require_int(row["aggregate_revision"]),
        )
        if pinned_revision is not None:
            effective = pinned_revision
        else:
            effective = identity.current_revision
        if effective is None:
            return GoverningSnapshot(
                identity=identity,
                effective_revision_number=None,
                revision=None,
                revision_state=None,
            )
        revision_row = fetch_revision(
            conn,
            tenant_id=tenant_id,
            asset_id=asset_id,
            revision_number=effective,
        )
        if revision_row is None:
            return GoverningSnapshot(
                identity=identity,
                effective_revision_number=effective,
                revision=None,
                revision_state=None,
            )
        revision = RevisionFacts(
            asset_revision_id=revision_row["asset_revision_id"],
            revision_number=_require_int(revision_row["revision_number"]),
            storage_key=revision_row["storage_key"],
            byte_size=_require_int(revision_row["byte_size"]),
            sha256=revision_row["sha256"],
        )
        state_row = fetch_revision_state(
            conn,
            tenant_id=tenant_id,
            asset_revision_id=revision.asset_revision_id,
        )
        if state_row is None:
            return GoverningSnapshot(
                identity=identity,
                effective_revision_number=effective,
                revision=revision,
                revision_state=None,
            )
        bytes_purged = state_row["bytes_purged"]
        if not isinstance(bytes_purged, bool):
            raise GovernanceUnavailableError(_GOVERNANCE_UNAVAILABLE)
        state = RevisionStateFacts(
            safety_state=parse_asset_revision_safety_state(state_row["safety_state"]),
            bytes_purged=bytes_purged,
        )
        return GoverningSnapshot(
            identity=identity,
            effective_revision_number=effective,
            revision=revision,
            revision_state=state,
        )

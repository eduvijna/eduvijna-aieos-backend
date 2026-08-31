"""Shared helpers for GCI-I02 PostgreSQL tests. Not production runtime."""

from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy import text

REPO_ROOT = Path(__file__).resolve().parents[1]


def set_tenant(conn, tenant_id: uuid.UUID) -> None:
    conn.execute(
        text("SELECT set_config('aieos.tenant_id', :tid, true)"),
        {"tid": str(tenant_id)},
    )


def clear_asset_audit_rows_for_schema_downgrade(engine) -> None:
    """TEST-ONLY isolation for historical Alembic cycle tests.

    Production downgrade paths remain fail-closed and never delete audit
    evidence. The shared pytest PostgreSQL is session-scoped; immutable
    asset.* and teaching.* rows would otherwise block unrelated downgrades.
    """
    with engine.begin() as conn:
        exists = conn.execute(
            text(
                "SELECT EXISTS ("
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'security' AND table_name = 'audit_records'"
                ")"
            )
        ).scalar()
        if not exists:
            return
        conn.execute(
            text(
                "ALTER TABLE security.audit_records "
                "DISABLE TRIGGER audit_records_immutable_delete"
            )
        )
        conn.execute(
            text(
                "DELETE FROM security.audit_records "
                "WHERE action LIKE 'asset.%' OR action LIKE 'teaching.%'"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE security.audit_records "
                "ENABLE TRIGGER audit_records_immutable_delete"
            )
        )

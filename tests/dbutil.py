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

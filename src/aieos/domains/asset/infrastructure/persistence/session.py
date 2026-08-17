"""Short-lived Asset current-use read transactions (not Content UoW)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError

from aieos.platform.governance.errors import GovernanceUnavailableError

_GOVERNANCE_UNAVAILABLE = "governance unavailable"


@contextmanager
def asset_authority_read(
    engine: Engine, *, query_tenant_id: UUID
) -> Iterator[Connection]:
    """Open a short transaction for Asset current-use reads.

    Installs transaction-local ``aieos.tenant_id`` as an RLS query scope only.
    Always rolls back so pool reuse cannot retain tenant scope or write intent.
    Does not bypass row-level security and does not disable RLS.
    """
    try:
        with engine.connect() as conn:
            trans = conn.begin()
            try:
                conn.execute(
                    text("SELECT set_config('aieos.tenant_id', :tid, true)"),
                    {"tid": str(query_tenant_id)},
                )
                yield conn
            finally:
                if trans.is_active:
                    trans.rollback()
    except GovernanceUnavailableError:
        raise
    except SQLAlchemyError as exc:
        raise GovernanceUnavailableError(_GOVERNANCE_UNAVAILABLE) from exc

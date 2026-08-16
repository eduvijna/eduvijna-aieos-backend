"""Short-lived security authority read transactions (not Content UoW)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from aieos.platform.security.context import AuthorizationUnavailableError


@contextmanager
def security_authority_read(
    engine: Engine, *, query_tenant_id: UUID | None = None
) -> Iterator[Connection]:
    """Open a short transaction for current-authority reads.

    When ``query_tenant_id`` is set, installs transaction-local
    ``aieos.tenant_id`` as an RLS *query scope* only — not trusted authority.
    Always rolls back so pool reuse cannot retain tenant scope or write intent.
    """
    try:
        with engine.connect() as conn:
            trans = conn.begin()
            try:
                if query_tenant_id is not None:
                    conn.execute(
                        text("SELECT set_config('aieos.tenant_id', :tid, true)"),
                        {"tid": str(query_tenant_id)},
                    )
                yield conn
            finally:
                if trans.is_active:
                    trans.rollback()
    except AuthorizationUnavailableError:
        raise
    except Exception as exc:
        raise AuthorizationUnavailableError(
            "authorization unavailable"
        ) from exc

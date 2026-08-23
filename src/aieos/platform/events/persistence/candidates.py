"""EVENT dispatcher outbox candidate repository (ADR-AIEOS-045)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Engine


@dataclass(frozen=True, slots=True)
class OutboxDispatchCandidate:
    tenant_id: UUID
    eligible_at: datetime


class SqlAlchemyOutboxCandidateRepository:
    """Calls ``integration.list_outbox_dispatch_candidates`` only.

    Returns ``tenant_id`` + ``eligible_at`` exclusively. Does not set tenant
    context, change role, or read outbox payload columns.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def list_candidates(
        self,
        *,
        limit: int,
        as_of: datetime,
    ) -> tuple[OutboxDispatchCandidate, ...]:
        if not isinstance(limit, int) or limit < 1 or limit > 1000:
            raise ValueError("limit must be an integer in 1..1000")
        if as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware")
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT tenant_id, eligible_at "
                    "FROM integration.list_outbox_dispatch_candidates(:limit, :as_of)"
                ),
                {"limit": limit, "as_of": as_of},
            ).mappings().all()
        return tuple(
            OutboxDispatchCandidate(
                tenant_id=UUID(str(row["tenant_id"])),
                eligible_at=row["eligible_at"],
            )
            for row in rows
        )

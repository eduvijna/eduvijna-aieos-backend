"""Outbox insert (Content UoW) and claim/publish finalization (dispatcher)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, or_, select, text, update
from sqlalchemy.engine import Connection, Engine

from aieos.domains.content.application.errors import (
    ContentApplicationError,
    PersistenceOperationFailed,
)
from aieos.platform.events.constants import (
    OUTBOX_CLAIMED,
    OUTBOX_PENDING,
    OUTBOX_PUBLISHED,
    OUTBOX_QUARANTINED,
)
from aieos.platform.events.identities import EventId
from aieos.platform.events.models import OutboxMessage
from aieos.platform.events.persistence.models import outbox_messages_table


def _reraise(exc: BaseException) -> None:
    if isinstance(exc, ContentApplicationError):
        raise exc
    raise PersistenceOperationFailed("outbox persistence operation failed") from exc


def _from_row(row: Any) -> OutboxMessage:
    return OutboxMessage(
        event_id=EventId(row.event_id),
        tenant_id=row.tenant_id,
        event_type=row.event_type,
        subject=row.subject,
        aggregate_type=row.aggregate_type,
        aggregate_id=row.aggregate_id,
        aggregate_revision=int(row.aggregate_revision),
        envelope=dict(row.envelope),
        status=row.status,
        attempt_count=int(row.attempt_count),
        available_at=row.available_at,
        claimed_by=row.claimed_by,
        claimed_until=row.claimed_until,
        published_at=row.published_at,
        broker_stream=row.broker_stream,
        broker_sequence=(
            None if row.broker_sequence is None else int(row.broker_sequence)
        ),
        last_error_code=row.last_error_code,
        created_at=row.created_at,
    )


class SqlAlchemyOutboxRepository:
    """INSERT-only path used inside Content Unit of Work."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, message: OutboxMessage) -> None:
        try:
            self._connection.execute(
                outbox_messages_table.insert().values(
                    event_id=message.event_id.value,
                    tenant_id=message.tenant_id,
                    event_type=message.event_type,
                    subject=message.subject,
                    aggregate_type=message.aggregate_type,
                    aggregate_id=message.aggregate_id,
                    aggregate_revision=message.aggregate_revision,
                    envelope=dict(message.envelope),
                    status=message.status,
                    attempt_count=message.attempt_count,
                    available_at=message.available_at,
                    claimed_by=message.claimed_by,
                    claimed_until=message.claimed_until,
                    published_at=message.published_at,
                    broker_stream=message.broker_stream,
                    broker_sequence=message.broker_sequence,
                    last_error_code=message.last_error_code,
                    created_at=message.created_at,
                )
            )
        except Exception as exc:
            _reraise(exc)


class SqlAlchemyOutboxDispatcherRepository:
    """Claim / publish / retry / quarantine. Never mutates Content tables."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def claim_once(
        self,
        *,
        tenant_id: UUID,
        claimed_by: str,
        now: datetime,
        claim_until: datetime,
    ) -> OutboxMessage | None:
        with self._engine.begin() as conn:
            self._set_tenant(conn, tenant_id)
            row = conn.execute(
                select(outbox_messages_table)
                .where(
                    or_(
                        and_(
                            outbox_messages_table.c.status == OUTBOX_PENDING,
                            outbox_messages_table.c.available_at <= now,
                        ),
                        and_(
                            outbox_messages_table.c.status == OUTBOX_CLAIMED,
                            outbox_messages_table.c.claimed_until <= now,
                        ),
                    )
                )
                .order_by(outbox_messages_table.c.available_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            ).one_or_none()
            if row is None:
                return None
            updated = conn.execute(
                update(outbox_messages_table)
                .where(outbox_messages_table.c.event_id == row.event_id)
                .values(
                    status=OUTBOX_CLAIMED,
                    claimed_by=claimed_by,
                    claimed_until=claim_until,
                    attempt_count=outbox_messages_table.c.attempt_count + 1,
                )
                .returning(outbox_messages_table)
            ).one()
            return _from_row(updated)

    def mark_published(
        self,
        *,
        tenant_id: UUID,
        event_id: UUID,
        claimed_by: str,
        attempt_count: int,
        published_at: datetime,
        broker_stream: str,
        broker_sequence: int,
    ) -> bool:
        with self._engine.begin() as conn:
            self._set_tenant(conn, tenant_id)
            result = conn.execute(
                update(outbox_messages_table)
                .where(
                    outbox_messages_table.c.event_id == event_id,
                    outbox_messages_table.c.status == OUTBOX_CLAIMED,
                    outbox_messages_table.c.claimed_by == claimed_by,
                    outbox_messages_table.c.attempt_count == attempt_count,
                )
                .values(
                    status=OUTBOX_PUBLISHED,
                    published_at=published_at,
                    broker_stream=broker_stream,
                    broker_sequence=broker_sequence,
                    claimed_by=None,
                    claimed_until=None,
                    last_error_code=None,
                )
            )
            return bool(result.rowcount)

    def release_for_retry(
        self,
        *,
        tenant_id: UUID,
        event_id: UUID,
        claimed_by: str,
        attempt_count: int,
        available_at: datetime,
        error_code: str,
        quarantine: bool,
    ) -> bool:
        with self._engine.begin() as conn:
            self._set_tenant(conn, tenant_id)
            result = conn.execute(
                update(outbox_messages_table)
                .where(
                    outbox_messages_table.c.event_id == event_id,
                    outbox_messages_table.c.status == OUTBOX_CLAIMED,
                    outbox_messages_table.c.claimed_by == claimed_by,
                    outbox_messages_table.c.attempt_count == attempt_count,
                )
                .values(
                    status=OUTBOX_QUARANTINED if quarantine else OUTBOX_PENDING,
                    available_at=available_at,
                    claimed_by=None,
                    claimed_until=None,
                    last_error_code=error_code,
                )
            )
            return bool(result.rowcount)

    def get(
        self,
        *,
        tenant_id: UUID,
        event_id: UUID,
    ) -> OutboxMessage | None:
        with self._engine.connect() as conn:
            self._set_tenant(conn, tenant_id)
            row = conn.execute(
                select(outbox_messages_table).where(
                    outbox_messages_table.c.event_id == event_id
                )
            ).one_or_none()
            if row is None:
                return None
            return _from_row(row)

    @staticmethod
    def _set_tenant(conn: Connection, tenant_id: UUID) -> None:
        conn.execute(
            text("SELECT set_config('aieos.tenant_id', :tid, true)"),
            {"tid": str(tenant_id)},
        )

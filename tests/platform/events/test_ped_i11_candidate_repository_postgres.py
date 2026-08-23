"""PED-I11 candidate repository over EVENT dispatcher LOGIN (PostgreSQL)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from aieos.platform.events.persistence.candidates import (
    SqlAlchemyOutboxCandidateRepository,
)
from tests.conftest import EVENT_DISPATCHER_USER
from tests.dbutil import set_tenant

pytestmark = pytest.mark.postgres_candidate_authority

_AS_OF = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)
_T_ELIGIBLE = datetime(1990, 1, 1, 0, 0, 0, tzinfo=UTC)


def _insert_pending(
    conn,
    *,
    tenant_id: uuid.UUID,
    available_at: datetime,
) -> None:
    eid = uuid.uuid7()
    aggregate_id = uuid.uuid7()
    set_tenant(conn, tenant_id)
    conn.execute(
        text(
            """
            INSERT INTO integration.outbox_messages (
                event_id, tenant_id, event_type, subject, aggregate_type,
                aggregate_id, aggregate_revision, envelope, status,
                attempt_count, available_at, claimed_by, claimed_until,
                published_at, broker_stream, broker_sequence, last_error_code,
                created_at
            ) VALUES (
                :event_id, :tenant_id, :event_type, :subject, 'content',
                :aggregate_id, 0,
                jsonb_build_object('secret', 'PED_I11_NO_LEAK'),
                'PENDING', 0, :available_at,
                NULL, NULL, NULL, NULL, NULL, NULL, :created_at
            )
            """
        ),
        {
            "event_id": eid,
            "tenant_id": tenant_id,
            "event_type": f"io.eduvijna.aieos.content.content.created.v1",
            "subject": f"content/{aggregate_id}",
            "aggregate_id": aggregate_id,
            "available_at": available_at,
            "created_at": available_at,
        },
    )


def test_candidate_repository_shape_and_order(
    event_dispatcher_engine, bootstrap_engine
) -> None:
    tenant_a = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa91")
    tenant_b = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbb92")
    with bootstrap_engine.begin() as conn:
        conn.execute(text("SET LOCAL ROLE aieos_content_owner"))
        _insert_pending(conn, tenant_id=tenant_a, available_at=_T_ELIGIBLE)
        _insert_pending(
            conn,
            tenant_id=tenant_b,
            available_at=_T_ELIGIBLE + timedelta(hours=1),
        )

    repo = SqlAlchemyOutboxCandidateRepository(event_dispatcher_engine)
    with event_dispatcher_engine.connect() as conn:
        assert conn.execute(text("SELECT current_user")).scalar_one() == (
            EVENT_DISPATCHER_USER
        )

    candidates = repo.list_candidates(limit=10, as_of=_AS_OF)
    ids = [c.tenant_id for c in candidates if c.tenant_id in {tenant_a, tenant_b}]
    assert ids == [tenant_a, tenant_b]
    for c in candidates:
        assert set(c.__dataclass_fields__) == {"tenant_id", "eligible_at"}
        assert c.eligible_at.tzinfo is not None

    limited = repo.list_candidates(limit=1, as_of=_AS_OF)
    assert len(limited) == 1
    assert limited[0].tenant_id == tenant_a


def test_candidate_repository_rejects_naive_as_of(event_dispatcher_engine) -> None:
    repo = SqlAlchemyOutboxCandidateRepository(event_dispatcher_engine)
    with pytest.raises(ValueError, match="timezone-aware"):
        repo.list_candidates(limit=1, as_of=datetime(2026, 1, 1))

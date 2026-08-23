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


def test_candidate_repository_shape_and_order(
    event_dispatcher_engine, bootstrap_engine
) -> None:
    tenant_a = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa91")
    tenant_b = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbb92")
    with bootstrap_engine.begin() as conn:
        conn.execute(text("SET LOCAL ROLE aieos_content_owner"))
        for tenant_id, available_at in (
            (tenant_a, _T_ELIGIBLE),
            (tenant_b, _T_ELIGIBLE + timedelta(hours=1)),
        ):
            set_tenant(conn, tenant_id)
            conn.execute(
                text(
                    """
                    INSERT INTO integration.outbox_messages (
                      event_id, tenant_id, aggregate_type, aggregate_id,
                      event_type, envelope, status, available_at, attempt_count
                    ) VALUES (
                      :eid, :tid, 'content', :aid,
                      'io.eduvijna.aieos.content.content.created.v1',
                      CAST(:env AS jsonb), 'PENDING', :avail, 0
                    )
                    """
                ),
                {
                    "eid": str(uuid.uuid7()),
                    "tid": str(tenant_id),
                    "aid": str(uuid.uuid7()),
                    "env": '{"secret":"PED_I11_NO_LEAK"}',
                    "avail": available_at,
                },
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

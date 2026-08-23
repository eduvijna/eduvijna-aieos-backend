"""PED-I11 wrong-stream / absent-stream outbox semantics."""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest

from aieos.platform.events.constants import (
    ERROR_NATS_PUBLISH_REJECTED,
    ERROR_NATS_STREAM_MISMATCH,
    OUTBOX_PUBLISHED,
    OUTBOX_QUARANTINED,
    PRODUCTION_EVENT_STREAM_NAME,
)
from aieos.platform.events.nats.dispatcher import (
    ContentOutboxDispatcher,
    OutboxDispatcherConfig,
)
from aieos.platform.events.nats.publisher import (
    NatsJetStreamEventPublisher,
    PublishAck,
    PublishResult,
)
from aieos.platform.events.persistence.repositories import (
    SqlAlchemyOutboxDispatcherRepository,
)
from tests.platform.events.helpers import outbox_rows
from tests.platform.workflows.helpers import client_for, create_content

pytestmark = pytest.mark.ped_i11


class _MismatchPublisher:
    async def publish(self, message):
        return PublishResult(
            published=False,
            ack=PublishAck(stream="WRONG_STREAM", sequence=1),
            error_code=ERROR_NATS_STREAM_MISMATCH,
            permanent=True,
        )


class _AbsentStreamPublisher:
    async def publish(self, message):
        return PublishResult(
            published=False,
            error_code=ERROR_NATS_PUBLISH_REJECTED,
            permanent=True,
        )


def test_wrong_stream_ack_does_not_mark_published(
    runtime_engine, event_dispatcher_engine, bootstrap_engine
) -> None:
    tenant_id = uuid.uuid7()
    client = client_for(runtime_engine, tenant_id, uuid.uuid7())
    created = create_content(client, tenant_id)
    dispatcher = ContentOutboxDispatcher(
        SqlAlchemyOutboxDispatcherRepository(event_dispatcher_engine),
        _MismatchPublisher(),  # type: ignore[arg-type]
        OutboxDispatcherConfig(
            claim_lease=timedelta(seconds=30),
            max_attempts=3,
            retry_delay=timedelta(milliseconds=1),
            claimed_by="ped-i11-wrong-stream",
        ),
    )
    published = asyncio.run(dispatcher.dispatch_once(tenant_id))
    assert published is False
    rows = outbox_rows(bootstrap_engine, content_id=created["content_id"])
    assert len(rows) == 1
    assert rows[0]["status"] != OUTBOX_PUBLISHED
    assert rows[0]["status"] == OUTBOX_QUARANTINED
    assert rows[0]["last_error_code"] == ERROR_NATS_STREAM_MISMATCH


def test_absent_stream_rejects_without_published(
    runtime_engine, event_dispatcher_engine, bootstrap_engine
) -> None:
    tenant_id = uuid.uuid7()
    client = client_for(runtime_engine, tenant_id, uuid.uuid7())
    created = create_content(client, tenant_id)
    dispatcher = ContentOutboxDispatcher(
        SqlAlchemyOutboxDispatcherRepository(event_dispatcher_engine),
        _AbsentStreamPublisher(),  # type: ignore[arg-type]
        OutboxDispatcherConfig(
            claim_lease=timedelta(seconds=30),
            max_attempts=3,
            retry_delay=timedelta(milliseconds=1),
            claimed_by="ped-i11-absent-stream",
        ),
    )
    published = asyncio.run(dispatcher.dispatch_once(tenant_id))
    assert published is False
    rows = outbox_rows(bootstrap_engine, content_id=created["content_id"])
    assert rows[0]["status"] != OUTBOX_PUBLISHED
    assert rows[0]["status"] == OUTBOX_QUARANTINED
    assert rows[0]["last_error_code"] == ERROR_NATS_PUBLISH_REJECTED


def test_expected_stream_mismatch_via_publisher_adapter() -> None:
    class _JS:
        async def publish(self, *args, **kwargs):
            return SimpleNamespace(stream="AIEOS_EVENTS", seq=3)

    class _Client:
        def jetstream(self):
            return _JS()

    publisher = NatsJetStreamEventPublisher(
        _Client(),  # type: ignore[arg-type]
        expected_stream=PRODUCTION_EVENT_STREAM_NAME,
    )
    msg = SimpleNamespace(
        event_id=uuid.uuid4(),
        event_type="io.eduvijna.aieos.content.content.created.v1",
        envelope={
            "specversion": "1.0",
            "id": str(uuid.uuid4()),
            "source": "urn:eduvijna:aieos:content",
            "type": "io.eduvijna.aieos.content.content.created.v1",
            "subject": "content/x",
            "time": "2026-08-23T00:00:00Z",
            "datacontenttype": "application/json",
            "data": {},
            "tenantid": str(uuid.uuid4()),
            "correlationid": str(uuid.uuid4()),
            "causationid": str(uuid.uuid4()),
            "actorid": str(uuid.uuid4()),
            "effectiveactorid": str(uuid.uuid4()),
            "aggregaterevision": 1,
        },
    )
    result = asyncio.run(publisher.publish(msg))  # type: ignore[arg-type]
    assert result.published is False
    assert result.error_code == ERROR_NATS_STREAM_MISMATCH
    assert result.ack is not None
    assert result.ack.stream == "AIEOS_EVENTS"

"""GCI-I08 outbox dispatcher claim fencing, publish, and JetStream integration."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError, ProgrammingError

from aieos.platform.events.constants import (
    ERROR_NATS_UNAVAILABLE,
    ERROR_RETRY_EXHAUSTED,
    OUTBOX_CLAIMED,
    OUTBOX_PENDING,
    OUTBOX_PUBLISHED,
    OUTBOX_QUARANTINED,
    TEST_STREAM_NAME,
)
from aieos.platform.events.models import OutboxMessage
from aieos.platform.events.nats.publisher import (
    NatsJetStreamEventPublisher,
    PublishAck,
    PublishResult,
)
from aieos.platform.events.persistence.repositories import (
    SqlAlchemyOutboxDispatcherRepository,
)
from tests.conftest import SCHEMA_OWNER_ROLE
from tests.dbutil import set_tenant
from tests.platform.events.helpers import (
    connect_nats,
    ensure_test_stream,
    envelope_bytes,
    make_dispatcher,
    nats_server_version,
    outbox_rows,
    run_async,
    start_nats,
    stop_nats,
)
from tests.platform.workflows.helpers import client_for, create_content

pytestmark = pytest.mark.gci_i08


class FakePublisher:
    def __init__(self, result: PublishResult):
        self.result = result
        self.calls: list[OutboxMessage] = []

    async def publish(self, message: OutboxMessage) -> PublishResult:
        self.calls.append(message)
        return self.result


class ClaimVisiblePublisher:
    """Proves publish runs after the claim transaction commits."""

    def __init__(self, bootstrap_engine: Engine, result: PublishResult):
        self._bootstrap = bootstrap_engine
        self.result = result
        self.calls: list[OutboxMessage] = []

    async def publish(self, message: OutboxMessage) -> PublishResult:
        rows = outbox_rows(
            self._bootstrap, content_id=str(message.aggregate_id)
        )
        row = next(row for row in rows if row["event_id"] == message.event_id.value)
        assert row["status"] == OUTBOX_CLAIMED
        assert row["claimed_by"] is not None
        self.calls.append(message)
        return self.result


class StalledPublisher:
    """Never completes until cancelled by the dispatcher publish timeout."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.calls: list[OutboxMessage] = []

    async def publish(self, message: OutboxMessage) -> PublishResult:
        self.calls.append(message)
        self.started.set()
        await asyncio.Event().wait()
        raise AssertionError("stalled publisher must not complete")


def _repo(engine: Engine) -> SqlAlchemyOutboxDispatcherRepository:
    return SqlAlchemyOutboxDispatcherRepository(engine)


def _pending_row(bootstrap_engine: Engine, content_id: str) -> dict:
    rows = outbox_rows(bootstrap_engine, content_id=content_id)
    assert len(rows) == 1
    return rows[0]


@pytest.fixture(scope="session")
def nats_url():
    url = start_nats()
    try:
        yield url
    finally:
        stop_nats()


class TestClaimAndFencing:
    def test_two_dispatchers_cannot_own_live_lease(
        self, runtime_engine, event_dispatcher_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = client_for(runtime_engine, tenant_id, uuid.uuid7())
        created = create_content(client, tenant_id)
        repo = _repo(event_dispatcher_engine)
        now = datetime.now(UTC)
        first = repo.claim_once(
            tenant_id=tenant_id,
            claimed_by="d1",
            now=now,
            claim_until=now + timedelta(seconds=30),
        )
        second = repo.claim_once(
            tenant_id=tenant_id,
            claimed_by="d2",
            now=now,
            claim_until=now + timedelta(seconds=30),
        )
        assert first is not None
        assert second is None
        row = _pending_row(bootstrap_engine, created["content_id"])
        assert row["status"] == OUTBOX_CLAIMED
        assert row["claimed_by"] == "d1"

    def test_expired_reclaim_increments_attempt(
        self, runtime_engine, event_dispatcher_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = client_for(runtime_engine, tenant_id, uuid.uuid7())
        created = create_content(client, tenant_id)
        repo = _repo(event_dispatcher_engine)
        now = datetime.now(UTC)
        first = repo.claim_once(
            tenant_id=tenant_id,
            claimed_by="d1",
            now=now,
            claim_until=now + timedelta(seconds=1),
        )
        assert first is not None
        reclaimed = repo.claim_once(
            tenant_id=tenant_id,
            claimed_by="d2",
            now=now + timedelta(seconds=2),
            claim_until=now + timedelta(seconds=32),
        )
        assert reclaimed is not None
        assert reclaimed.claimed_by == "d2"
        assert reclaimed.attempt_count == 2

    def test_stale_claimant_cannot_finalize(
        self, runtime_engine, event_dispatcher_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = client_for(runtime_engine, tenant_id, uuid.uuid7())
        created = create_content(client, tenant_id)
        repo = _repo(event_dispatcher_engine)
        now = datetime.now(UTC)
        claim_a = repo.claim_once(
            tenant_id=tenant_id,
            claimed_by="A",
            now=now,
            claim_until=now + timedelta(seconds=1),
        )
        assert claim_a is not None
        attempt_a = claim_a.attempt_count
        claim_b = repo.claim_once(
            tenant_id=tenant_id,
            claimed_by="B",
            now=now + timedelta(seconds=2),
            claim_until=now + timedelta(seconds=32),
        )
        assert claim_b is not None
        assert not repo.mark_published(
            tenant_id=tenant_id,
            event_id=claim_a.event_id.value,
            claimed_by="A",
            attempt_count=attempt_a,
            published_at=now + timedelta(seconds=3),
            broker_stream=TEST_STREAM_NAME,
            broker_sequence=1,
        )
        assert not repo.release_for_retry(
            tenant_id=tenant_id,
            event_id=claim_a.event_id.value,
            claimed_by="A",
            attempt_count=attempt_a,
            available_at=now + timedelta(seconds=10),
            error_code=ERROR_NATS_UNAVAILABLE,
            quarantine=False,
        )

    def test_stale_claimant_cannot_quarantine_after_publish(
        self, runtime_engine, event_dispatcher_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = client_for(runtime_engine, tenant_id, uuid.uuid7())
        created = create_content(client, tenant_id)
        repo = _repo(event_dispatcher_engine)
        now = datetime.now(UTC)
        claim_a = repo.claim_once(
            tenant_id=tenant_id,
            claimed_by="A",
            now=now,
            claim_until=now + timedelta(seconds=1),
        )
        assert claim_a is not None
        attempt_a = claim_a.attempt_count
        claim_b = repo.claim_once(
            tenant_id=tenant_id,
            claimed_by="B",
            now=now + timedelta(seconds=2),
            claim_until=now + timedelta(seconds=32),
        )
        assert claim_b is not None
        assert repo.mark_published(
            tenant_id=tenant_id,
            event_id=claim_b.event_id.value,
            claimed_by="B",
            attempt_count=claim_b.attempt_count,
            published_at=now + timedelta(seconds=3),
            broker_stream=TEST_STREAM_NAME,
            broker_sequence=42,
        )
        assert not repo.release_for_retry(
            tenant_id=tenant_id,
            event_id=claim_a.event_id.value,
            claimed_by="A",
            attempt_count=attempt_a,
            available_at=now + timedelta(seconds=10),
            error_code=ERROR_RETRY_EXHAUSTED,
            quarantine=True,
        )
        row = _pending_row(bootstrap_engine, created["content_id"])
        assert row["status"] == OUTBOX_PUBLISHED


class TestDispatchSemantics:
    def test_publish_runs_after_committed_claim(
        self, runtime_engine, event_dispatcher_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = client_for(runtime_engine, tenant_id, uuid.uuid7())
        create_content(client, tenant_id)
        publisher = ClaimVisiblePublisher(
            bootstrap_engine,
            PublishResult(
                published=True,
                ack=PublishAck(stream=TEST_STREAM_NAME, sequence=7),
            ),
        )
        dispatcher = make_dispatcher(event_dispatcher_engine, publisher)
        assert run_async(dispatcher.dispatch_once(tenant_id)) is True
        assert len(publisher.calls) == 1

    def test_transient_nats_failure_returns_pending_retryable(
        self, runtime_engine, event_dispatcher_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = client_for(runtime_engine, tenant_id, uuid.uuid7())
        created = create_content(client, tenant_id)
        publisher = FakePublisher(
            PublishResult(published=False, error_code=ERROR_NATS_UNAVAILABLE, permanent=False)
        )
        dispatcher = make_dispatcher(event_dispatcher_engine, publisher, max_attempts=3)
        assert run_async(dispatcher.dispatch_once(tenant_id)) is False
        row = _pending_row(bootstrap_engine, created["content_id"])
        assert row["status"] == OUTBOX_PENDING
        assert row["last_error_code"] == ERROR_NATS_UNAVAILABLE

    def test_max_attempts_quarantines_with_retry_exhausted(
        self, runtime_engine, event_dispatcher_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = client_for(runtime_engine, tenant_id, uuid.uuid7())
        created = create_content(client, tenant_id)
        publisher = FakePublisher(
            PublishResult(published=False, error_code=ERROR_NATS_UNAVAILABLE, permanent=False)
        )
        dispatcher = make_dispatcher(
            event_dispatcher_engine, publisher, max_attempts=1
        )
        assert run_async(dispatcher.dispatch_once(tenant_id)) is False
        row = _pending_row(bootstrap_engine, created["content_id"])
        assert row["status"] == OUTBOX_QUARANTINED
        assert row["last_error_code"] == ERROR_RETRY_EXHAUSTED

    def test_published_cannot_regress_via_release_for_retry(
        self, runtime_engine, event_dispatcher_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = client_for(runtime_engine, tenant_id, uuid.uuid7())
        created = create_content(client, tenant_id)
        repo = _repo(event_dispatcher_engine)
        now = datetime.now(UTC)
        claimed = repo.claim_once(
            tenant_id=tenant_id,
            claimed_by="d1",
            now=now,
            claim_until=now + timedelta(seconds=30),
        )
        assert claimed is not None
        assert repo.mark_published(
            tenant_id=tenant_id,
            event_id=claimed.event_id.value,
            claimed_by="d1",
            attempt_count=claimed.attempt_count,
            published_at=now,
            broker_stream=TEST_STREAM_NAME,
            broker_sequence=99,
        )
        assert not repo.release_for_retry(
            tenant_id=tenant_id,
            event_id=claimed.event_id.value,
            claimed_by="d1",
            attempt_count=claimed.attempt_count,
            available_at=now + timedelta(seconds=5),
            error_code=ERROR_NATS_UNAVAILABLE,
            quarantine=False,
        )
        row = _pending_row(bootstrap_engine, created["content_id"])
        assert row["status"] == OUTBOX_PUBLISHED


class TestPublishTimeout:
    def test_stalled_publisher_times_out_to_pending(
        self, runtime_engine, event_dispatcher_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = client_for(runtime_engine, tenant_id, uuid.uuid7())
        created = create_content(client, tenant_id)
        before = _pending_row(bootstrap_engine, created["content_id"])
        publisher = StalledPublisher()
        dispatcher = make_dispatcher(
            event_dispatcher_engine,
            publisher,
            max_attempts=3,
            publish_timeout_seconds=0.05,
        )
        assert run_async(dispatcher.dispatch_once(tenant_id)) is False
        row = _pending_row(bootstrap_engine, created["content_id"])
        assert row["status"] == OUTBOX_PENDING
        assert row["last_error_code"] == ERROR_NATS_UNAVAILABLE
        assert int(row["attempt_count"]) == 1
        assert row["event_id"] == before["event_id"]
        assert dict(row["envelope"]) == dict(before["envelope"])
        with bootstrap_engine.connect() as conn:
            content = conn.execute(
                text(
                    "SELECT content_id FROM content.contents WHERE content_id = :cid"
                ),
                {"cid": created["content_id"]},
            ).one_or_none()
        assert content is not None

    def test_stalled_publisher_exhausts_to_quarantined(
        self, runtime_engine, event_dispatcher_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = client_for(runtime_engine, tenant_id, uuid.uuid7())
        created = create_content(client, tenant_id)
        before = _pending_row(bootstrap_engine, created["content_id"])
        publisher = StalledPublisher()
        dispatcher = make_dispatcher(
            event_dispatcher_engine,
            publisher,
            max_attempts=1,
            publish_timeout_seconds=0.05,
        )
        assert run_async(dispatcher.dispatch_once(tenant_id)) is False
        row = _pending_row(bootstrap_engine, created["content_id"])
        assert row["status"] == OUTBOX_QUARANTINED
        assert row["last_error_code"] == ERROR_RETRY_EXHAUSTED
        assert row["event_id"] == before["event_id"]
        assert dict(row["envelope"]) == dict(before["envelope"])

    def test_timeout_finalization_loses_to_reclaimed_published_claim(
        self, runtime_engine, event_dispatcher_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = client_for(runtime_engine, tenant_id, uuid.uuid7())
        created = create_content(client, tenant_id)
        before = _pending_row(bootstrap_engine, created["content_id"])
        publisher = StalledPublisher()
        repo = _repo(event_dispatcher_engine)
        claim_started = datetime.now(UTC)

        async def race() -> bool:
            dispatcher_a = make_dispatcher(
                event_dispatcher_engine,
                publisher,
                claimed_by="A",
                max_attempts=3,
                claim_lease=timedelta(milliseconds=50),
                publish_timeout_seconds=0.4,
            )
            task = asyncio.create_task(dispatcher_a.dispatch_once(tenant_id))
            await publisher.started.wait()
            claim_b = repo.claim_once(
                tenant_id=tenant_id,
                claimed_by="B",
                now=claim_started + timedelta(seconds=1),
                claim_until=claim_started + timedelta(seconds=31),
            )
            assert claim_b is not None
            assert claim_b.attempt_count == 2
            assert repo.mark_published(
                tenant_id=tenant_id,
                event_id=claim_b.event_id.value,
                claimed_by="B",
                attempt_count=claim_b.attempt_count,
                published_at=claim_started + timedelta(seconds=2),
                broker_stream=TEST_STREAM_NAME,
                broker_sequence=42,
            )
            return await task

        assert run_async(race()) is False
        row = _pending_row(bootstrap_engine, created["content_id"])
        assert row["status"] == OUTBOX_PUBLISHED
        assert int(row["attempt_count"]) == 2
        assert row["event_id"] == before["event_id"]
        assert dict(row["envelope"]) == dict(before["envelope"])
        assert row["last_error_code"] is None


class TestOutboxImmutabilityAndPrivileges:
    def test_immutable_envelope_and_event_type(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = client_for(runtime_engine, tenant_id, uuid.uuid7())
        created = create_content(client, tenant_id)
        row = _pending_row(bootstrap_engine, created["content_id"])
        with bootstrap_engine.begin() as conn:
            with pytest.raises(OperationalError):
                conn.execute(
                    text(
                        "UPDATE integration.outbox_messages SET envelope = '{}'::jsonb "
                        "WHERE event_id = :id"
                    ),
                    {"id": row["event_id"]},
                )
        with bootstrap_engine.begin() as conn:
            conn.execute(text(f"SET LOCAL ROLE {SCHEMA_OWNER_ROLE}"))
            set_tenant(conn, row["tenant_id"])
            with pytest.raises(OperationalError):
                conn.execute(
                    text(
                        "UPDATE integration.outbox_messages SET event_type = 'mutated' "
                        "WHERE event_id = :id"
                    ),
                    {"id": row["event_id"]},
                )

    def test_runtime_cannot_read_or_mutate_outbox(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = client_for(runtime_engine, tenant_id, uuid.uuid7())
        created = create_content(client, tenant_id)
        row = _pending_row(bootstrap_engine, created["content_id"])
        with runtime_engine.connect() as conn:
            set_tenant(conn, tenant_id)
            with pytest.raises(ProgrammingError):
                conn.execute(text("SELECT count(*) FROM integration.outbox_messages"))
        with runtime_engine.connect() as conn:
            set_tenant(conn, tenant_id)
            with pytest.raises(ProgrammingError):
                conn.execute(
                    text(
                        "UPDATE integration.outbox_messages SET status = 'PUBLISHED' "
                        "WHERE event_id = :id"
                    ),
                    {"id": row["event_id"]},
                )
        other_tenant = uuid.uuid7()
        with runtime_engine.begin() as conn:
            set_tenant(conn, tenant_id)
            with pytest.raises(ProgrammingError):
                conn.execute(
                    text(
                        """
                        INSERT INTO integration.outbox_messages (
                            event_id, tenant_id, event_type, subject, aggregate_type,
                            aggregate_id, aggregate_revision, envelope, status,
                            attempt_count, available_at, created_at
                        ) VALUES (
                            :event_id, :tenant_id, 'io.eduvijna.aieos.content.content.created.v1',
                            'content/x', 'content', :aggregate_id, 0, '{}'::jsonb,
                            'PENDING', 0, now(), now()
                        )
                        """
                    ),
                    {
                        "event_id": uuid.uuid7(),
                        "tenant_id": other_tenant,
                        "aggregate_id": uuid.uuid7(),
                    },
                )

    def test_event_dispatcher_cannot_insert_delete_or_touch_content(
        self, runtime_engine, event_dispatcher_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = client_for(runtime_engine, tenant_id, uuid.uuid7())
        created = create_content(client, tenant_id)
        row = _pending_row(bootstrap_engine, created["content_id"])
        with event_dispatcher_engine.connect() as conn:
            set_tenant(conn, tenant_id)
            with pytest.raises(ProgrammingError):
                conn.execute(
                    text(
                        """
                        INSERT INTO integration.outbox_messages (
                            event_id, tenant_id, event_type, subject, aggregate_type,
                            aggregate_id, aggregate_revision, envelope, status,
                            attempt_count, available_at, created_at
                        ) VALUES (
                            :event_id, :tenant_id, 'io.eduvijna.aieos.content.content.created.v1',
                            'content/x', 'content', :aggregate_id, 99, '{}'::jsonb,
                            'PENDING', 0, now(), now()
                        )
                        """
                    ),
                    {
                        "event_id": uuid.uuid7(),
                        "tenant_id": tenant_id,
                        "aggregate_id": uuid.uuid7(),
                    },
                )
        with event_dispatcher_engine.connect() as conn:
            set_tenant(conn, tenant_id)
            with pytest.raises(ProgrammingError):
                conn.execute(
                    text(
                        "DELETE FROM integration.outbox_messages WHERE event_id = :id"
                    ),
                    {"id": row["event_id"]},
                )
        with event_dispatcher_engine.connect() as conn:
            set_tenant(conn, tenant_id)
            with pytest.raises(ProgrammingError):
                conn.execute(
                    text(
                        "UPDATE content.contents SET title = 'blocked' "
                        "WHERE content_id = :cid"
                    ),
                    {"cid": created["content_id"]},
                )
        with event_dispatcher_engine.connect() as conn:
            set_tenant(conn, tenant_id)
            with pytest.raises(ProgrammingError):
                conn.execute(
                    text(
                        "UPDATE content.content_versions SET schema_id = 'blocked' "
                        "WHERE content_id = :cid"
                    ),
                    {"cid": created["content_id"]},
                )
        with event_dispatcher_engine.connect() as conn:
            set_tenant(conn, tenant_id)
            with pytest.raises(ProgrammingError):
                conn.execute(
                    text(
                        "UPDATE content.review_decisions SET decision = 'BLOCKED' "
                        "WHERE content_id = :cid"
                    ),
                    {"cid": created["content_id"]},
                )
        with event_dispatcher_engine.connect() as conn:
            set_tenant(conn, tenant_id)
            with pytest.raises(ProgrammingError):
                conn.execute(
                    text(
                        "UPDATE workflow.workflow_start_intents SET status = 'DELIVERED' "
                        "WHERE 1=1"
                    )
                )

    def test_missing_tenant_context_fails_closed(
        self, event_dispatcher_engine
    ) -> None:
        with event_dispatcher_engine.connect() as conn:
            with pytest.raises(ProgrammingError):
                conn.execute(text("SELECT count(*) FROM integration.outbox_messages"))


class TestJetStreamIntegration:
    def test_real_jetstream_publish_ack_and_headers(
        self,
        runtime_engine,
        event_dispatcher_engine,
        bootstrap_engine,
        nats_url,
    ) -> None:
        assert "2.14.3" in nats_server_version()
        tenant_id = uuid.uuid7()
        client = client_for(runtime_engine, tenant_id, uuid.uuid7())
        created = create_content(client, tenant_id)

        async def run_test() -> None:
            nats = await connect_nats(nats_url)
            await ensure_test_stream(nats)
            try:
                publisher = NatsJetStreamEventPublisher(nats)
                dispatcher = make_dispatcher(event_dispatcher_engine, publisher)
                assert await dispatcher.dispatch_once(tenant_id) is True
                row = _pending_row(bootstrap_engine, created["content_id"])
                assert row["status"] == OUTBOX_PUBLISHED
                assert row["broker_stream"] == TEST_STREAM_NAME
                assert row["broker_sequence"] is not None
                js = nats.jetstream()
                msg = await js.get_msg(TEST_STREAM_NAME, int(row["broker_sequence"]))
                assert msg.subject == row["event_type"]
                assert msg.data == envelope_bytes(row)
                assert msg.header.get("Nats-Msg-Id") == str(row["event_id"])
            finally:
                await nats.close()

        run_async(run_test())

    def test_broker_outage_leaves_business_content_and_pending_outbox(
        self, runtime_engine, event_dispatcher_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = client_for(runtime_engine, tenant_id, uuid.uuid7())
        created = create_content(client, tenant_id)
        publisher = FakePublisher(
            PublishResult(published=False, error_code=ERROR_NATS_UNAVAILABLE, permanent=False)
        )
        dispatcher = make_dispatcher(event_dispatcher_engine, publisher)
        assert run_async(dispatcher.dispatch_once(tenant_id)) is False
        with bootstrap_engine.connect() as conn:
            content_exists = conn.execute(
                text(
                    "SELECT count(*) FROM content.contents WHERE content_id = :cid"
                ),
                {"cid": created["content_id"]},
            ).scalar_one()
        assert int(content_exists) == 1
        row = _pending_row(bootstrap_engine, created["content_id"])
        assert row["status"] == OUTBOX_PENDING
        assert row["last_error_code"] == ERROR_NATS_UNAVAILABLE


class TestAtLeastOnceDelivery:
    """Crash between publish and mark_published may duplicate broker delivery (at-least-once)."""

    def test_reclaim_republishes_same_event_then_publishes(
        self,
        runtime_engine,
        event_dispatcher_engine,
        bootstrap_engine,
        nats_url,
    ) -> None:
        tenant_id = uuid.uuid7()
        client = client_for(runtime_engine, tenant_id, uuid.uuid7())
        created = create_content(client, tenant_id)
        row = _pending_row(bootstrap_engine, created["content_id"])
        event_id = row["event_id"]
        repo = _repo(event_dispatcher_engine)

        async def run_test() -> None:
            nats = await connect_nats(nats_url)
            await ensure_test_stream(nats)
            try:
                publisher = NatsJetStreamEventPublisher(nats)
                now = datetime.now(UTC)
                claimed = repo.claim_once(
                    tenant_id=tenant_id,
                    claimed_by="crash-a",
                    now=now,
                    claim_until=now + timedelta(seconds=1),
                )
                assert claimed is not None
                first_publish = await publisher.publish(claimed)
                assert first_publish.published is True
                dispatcher = make_dispatcher(
                    event_dispatcher_engine,
                    publisher,
                    claimed_by="crash-b",
                )
                dispatcher._clock = lambda: now + timedelta(seconds=2)
                assert await dispatcher.dispatch_once(tenant_id) is True
            finally:
                await nats.close()

        run_async(run_test())
        final = _pending_row(bootstrap_engine, created["content_id"])
        assert final["status"] == OUTBOX_PUBLISHED
        assert final["event_id"] == event_id


class TestForbiddenPersistenceArtifacts:
    def test_no_consumer_inbox_audit_or_asset_ref_tables(
        self, bootstrap_engine
    ) -> None:
        with bootstrap_engine.connect() as conn:
            tables = {
                f"{row.table_schema}.{row.table_name}"
                for row in conn.execute(
                    text(
                        """
                        SELECT table_schema, table_name
                        FROM information_schema.tables
                        WHERE table_schema IN ('content', 'integration', 'workflow', 'api')
                        """
                    )
                )
            }
        forbidden = {
            "integration.consumer_inbox",
            "content.audit_events",
        }
        assert forbidden.isdisjoint(tables)
        assert "content.publications" in tables
        assert "content.version_asset_refs" in tables

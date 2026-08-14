"""Shared helpers for GCI-I08 outbox / CloudEvents tests."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import nats
from nats.aio.client import Client as NATSClient
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.platform.events.cloudevents import canonical_cloudevent_bytes
from aieos.platform.events.constants import (
    CLOUDEVENTS_SOURCE,
    CLOUDEVENTS_SPECVERSION,
    TEST_STREAM_NAME,
    TEST_STREAM_SUBJECTS,
)
from aieos.platform.events.nats.dispatcher import (
    ContentOutboxDispatcher,
    OutboxDispatcherConfig,
)
from aieos.platform.events.nats.publisher import NatsJetStreamEventPublisher
from aieos.platform.events.persistence.repositories import (
    SqlAlchemyOutboxDispatcherRepository,
)
from tests.dbutil import REPO_ROOT
from tests.platform.workflows.helpers import client_for as _client_for

CONTRACTS_DIR = REPO_ROOT / "contracts" / "events" / "content"
NATS_IMAGE = "nats:2.14.3"
NATS_CONTAINER = "aieos-gci-i08-nats"
NATS_HOST_PORT = os.environ.get("AIEOS_TEST_NATS_PORT", "54222")

REQUIRED_ENVELOPE_KEYS = frozenset(
    {
        "specversion",
        "id",
        "source",
        "type",
        "subject",
        "time",
        "datacontenttype",
        "data",
        "tenantid",
        "correlationid",
        "causationid",
        "actorid",
        "effectiveactorid",
        "aggregaterevision",
    }
)

SENSITIVE_MARKERS = (
    "SENSITIVE_TEST_COMMENT",
    "reason_code_should_not_leak",
    "Idempotency-Key",
    "Bearer ",
    "eyJ",
    '"marker"',
    "Title",
    "Description",
    "role:",
    "workflow_instance_id",
    "temporal_workflow_id",
)


def run_async(coro):
    return asyncio.run(coro)


def client_for(runtime_engine: Engine, tenant_id: UUID, principal_id: UUID, **kw):
    return _client_for(runtime_engine, tenant_id, principal_id, **kw)


def outbox_rows(bootstrap_engine: Engine, *, content_id: str | None = None) -> list[dict[str, Any]]:
    sql = """
        SELECT event_id, tenant_id, event_type, subject, aggregate_type, aggregate_id,
               aggregate_revision, envelope, status, attempt_count, available_at,
               claimed_by, claimed_until, published_at, broker_stream, broker_sequence,
               last_error_code, created_at
        FROM integration.outbox_messages
    """
    params: dict[str, object] = {}
    if content_id is not None:
        sql += " WHERE aggregate_id = :cid"
        params["cid"] = content_id
    sql += " ORDER BY aggregate_revision, created_at, event_id"
    with bootstrap_engine.connect() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
    return [dict(row) for row in rows]


def assert_contract_compatible(envelope: dict[str, object], *, event_type: str) -> None:
    missing = REQUIRED_ENVELOPE_KEYS - set(envelope)
    assert not missing, missing
    assert envelope["specversion"] == CLOUDEVENTS_SPECVERSION
    assert envelope["source"] == CLOUDEVENTS_SOURCE
    assert envelope["type"] == event_type
    assert str(envelope["subject"]).startswith("content/")
    assert envelope["datacontenttype"] == "application/json"
    assert isinstance(envelope["data"], dict)
    assert isinstance(envelope["aggregaterevision"], int)
    contract_path = CONTRACTS_DIR / f"{event_type.rsplit('.', 2)[0].split('content.', 1)[-1]}.json"
    # Map type suffix to contract filename.
    suffix = event_type.removeprefix("io.eduvijna.aieos.content.")
    contract_path = CONTRACTS_DIR / f"{suffix}.json"
    example = json.loads(contract_path.read_text(encoding="utf-8"))
    assert set(example) == set(envelope)
    assert set(example["data"]) == set(envelope["data"])
    for key in REQUIRED_ENVELOPE_KEYS - {"data", "id", "time", "subject"}:
        if key in {"tenantid", "correlationid", "causationid", "actorid", "effectiveactorid"}:
            continue
        if key == "type":
            assert envelope[key] == example[key]
        elif key == "aggregaterevision":
            assert isinstance(envelope[key], int)
        else:
            assert envelope[key] == example[key]


def assert_no_sensitive_material(envelope: dict[str, object]) -> None:
    blob = json.dumps(envelope, sort_keys=True)
    for marker in SENSITIVE_MARKERS:
        assert marker not in blob, marker


def start_nats() -> str:
    subprocess.run(["docker", "rm", "-f", NATS_CONTAINER], check=False, capture_output=True)
    result = subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            NATS_CONTAINER,
            "-p",
            f"{NATS_HOST_PORT}:4222",
            NATS_IMAGE,
            "-js",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    if not result.stdout.strip():
        raise RuntimeError(f"nats docker run failed: {result.stderr}")
    return f"nats://127.0.0.1:{NATS_HOST_PORT}"


def stop_nats() -> None:
    subprocess.run(["docker", "rm", "-f", NATS_CONTAINER], check=False, capture_output=True)


async def connect_nats(url: str, *, attempts: int = 40) -> NATSClient:
    last: Exception | None = None
    for _ in range(attempts):
        try:
            client = await nats.connect(url, connect_timeout=1)
            return client
        except Exception as exc:  # noqa: BLE001
            last = exc
            await asyncio.sleep(0.25)
    raise RuntimeError(f"NATS did not become ready: {last}")


async def ensure_test_stream(client: NATSClient) -> None:
    js = client.jetstream()
    try:
        await js.delete_stream(TEST_STREAM_NAME)
    except Exception:  # noqa: BLE001
        pass
    await js.add_stream(name=TEST_STREAM_NAME, subjects=list(TEST_STREAM_SUBJECTS))


def make_dispatcher(
    engine: Engine,
    publisher,
    *,
    claimed_by: str = "event-dispatcher-a",
    max_attempts: int = 3,
    claim_lease: timedelta = timedelta(seconds=30),
    retry_delay: timedelta = timedelta(milliseconds=1),
    publish_timeout_seconds: float = 10.0,
) -> ContentOutboxDispatcher:
    return ContentOutboxDispatcher(
        SqlAlchemyOutboxDispatcherRepository(engine),
        publisher,
        OutboxDispatcherConfig(
            claim_lease=claim_lease,
            max_attempts=max_attempts,
            retry_delay=retry_delay,
            claimed_by=claimed_by,
            publish_timeout_seconds=publish_timeout_seconds,
        ),
    )


def envelope_bytes(row: dict[str, Any]) -> bytes:
    return canonical_cloudevent_bytes(dict(row["envelope"]))


def nats_server_version() -> str:
    result = subprocess.run(
        ["docker", "exec", NATS_CONTAINER, "nats-server", "--version"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() or result.stderr.strip()

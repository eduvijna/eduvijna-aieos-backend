"""PED-I11R1 NATS initial-connect outer deadline tests."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from aieos.platform.events.nats.connection import connect_event_dispatcher_nats
from aieos.platform.events.nats.credentials import InMemoryNatsCredentials
from aieos.platform.runtime.config_event_dispatcher import EventDispatcherRuntimeConfig
from aieos.platform.runtime.models import DeploymentEnvironment, ReleaseIdentity

pytestmark = pytest.mark.ped_i11

_SECRET_MATERIAL = (
    "-----BEGIN NATS USER JWT-----\n"
    "eyJhbGciOiJub25lIn0.e30.SECRET_JWT_PAYLOAD\n"
    "------END NATS USER JWT------\n"
    "-----BEGIN USER NKEY SEED-----\n"
    "SUAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\n"
    "------END USER NKEY SEED------\n"
)


def _config(*, connect_timeout: int = 1) -> EventDispatcherRuntimeConfig:
    return EventDispatcherRuntimeConfig(
        environment=DeploymentEnvironment.PRODUCTION,
        release_identity=ReleaseIdentity(
            application_version="0.1.0",
            git_sha="a" * 40,
            build_id="b1",
            artifact_digest="sha256:" + ("c" * 64),
        ),
        database_url="postgresql+psycopg://u:p@localhost/db",
        database_role="aieos_event_dispatcher",
        database_connect_timeout_seconds=5,
        nats_url="tls://nats.example:4222",
        nats_credentials=_SECRET_MATERIAL,
        nats_connect_timeout_seconds=connect_timeout,
        nats_ca_bundle_path=None,
        poll_interval_seconds=1,
        candidate_batch_size=10,
        max_messages_per_tenant_per_pass=1,
        claim_lease_seconds=30,
        max_attempts=3,
        retry_delay_seconds=1,
        publish_timeout_seconds=5,
        shutdown_grace_seconds=5,
    )


def test_outer_deadline_terminates_hanging_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed = {"done": False}

    class _HangClient:
        def __init__(self) -> None:
            self.options: dict = {}

        async def connect(self, *args, **kwargs) -> None:
            # Hang longer than outer deadline; ignore inner connect_timeout.
            await asyncio.Event().wait()

        async def close(self) -> None:
            closed["done"] = True

    monkeypatch.setattr(
        "aieos.platform.events.nats.connection.NATSClient",
        _HangClient,
    )
    # Bypass real seed validation — inject a credentials stub.
    creds = SimpleNamespace(
        user_jwt_cb=lambda: b"jwt",
        signature_cb=lambda nonce: b"c2ln",
        wipe=lambda: None,
    )

    async def _run() -> None:
        with pytest.raises(TimeoutError, match="initial connection deadline") as ei:
            await connect_event_dispatcher_nats(_config(connect_timeout=1), creds)  # type: ignore[arg-type]
        err = str(ei.value)
        assert "SECRET_JWT" not in err
        assert "SUAAAA" not in err

    asyncio.run(_run())
    assert closed["done"] is True


def test_successful_connect_returns_and_keeps_reconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _OkClient:
        def __init__(self) -> None:
            self.options: dict = {}

        async def connect(self, *args, **kwargs) -> None:
            assert kwargs.get("allow_reconnect") is True
            self.options["allow_reconnect"] = kwargs.get("allow_reconnect", False)
            await asyncio.sleep(0)

        async def close(self) -> None:
            raise AssertionError("successful connect must not close")

    monkeypatch.setattr(
        "aieos.platform.events.nats.connection.NATSClient",
        _OkClient,
    )
    creds = SimpleNamespace(
        user_jwt_cb=lambda: b"jwt",
        signature_cb=lambda nonce: b"c2ln",
        wipe=lambda: None,
    )

    async def _run():
        return await connect_event_dispatcher_nats(
            _config(connect_timeout=5),
            creds,  # type: ignore[arg-type]
        )

    client = asyncio.run(_run())
    assert client.options.get("allow_reconnect") is True

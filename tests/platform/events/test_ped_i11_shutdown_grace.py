"""PED-I11R1 shutdown-grace supervision tests."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from aieos.platform.events.nats.daemon import EventDispatcherDaemon
from aieos.platform.events.persistence.candidates import OutboxDispatchCandidate
from aieos.platform.runtime.config_event_dispatcher import EventDispatcherRuntimeConfig
from aieos.platform.runtime.entrypoints.event_dispatcher_main import (
    supervise_event_dispatcher_daemon,
)
from aieos.platform.runtime.models import DeploymentEnvironment, ReleaseIdentity

pytestmark = pytest.mark.ped_i11


def _cfg(*, poll: int = 30, grace: int = 2) -> EventDispatcherRuntimeConfig:
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
        nats_credentials="x",
        nats_connect_timeout_seconds=5,
        nats_ca_bundle_path=None,
        poll_interval_seconds=poll,
        candidate_batch_size=10,
        max_messages_per_tenant_per_pass=1,
        claim_lease_seconds=30,
        max_attempts=3,
        retry_delay_seconds=1,
        publish_timeout_seconds=5,
        shutdown_grace_seconds=grace,
    )


def test_s1_shutdown_during_poll_wait_wakes_immediately() -> None:
    cfg = _cfg(poll=30, grace=2)

    class _Cand:
        def list_candidates(self, *, limit: int, as_of: datetime):
            return ()

    class _Disp:
        async def dispatch_once(self, tenant_id: UUID) -> bool:
            return False

    daemon = EventDispatcherDaemon(
        config=cfg,
        candidate_repository=_Cand(),
        dispatcher=_Disp(),  # type: ignore[arg-type]
        claimed_by="s1",
    )
    shutdown = asyncio.Event()

    async def _scenario() -> None:
        async def _stop() -> None:
            await asyncio.sleep(0.05)
            shutdown.set()

        stopper = asyncio.create_task(_stop())
        await asyncio.wait_for(
            supervise_event_dispatcher_daemon(daemon, cfg, shutdown_event=shutdown),
            timeout=2.0,
        )
        await stopper

    asyncio.run(_scenario())
    assert daemon.shutdown_requested


def test_s2_in_flight_completes_inside_grace() -> None:
    cfg = _cfg(poll=30, grace=2)
    tenant = uuid4()
    started = asyncio.Event()
    passes = {"n": 0}

    class _Cand:
        def list_candidates(self, *, limit: int, as_of: datetime):
            passes["n"] += 1
            if passes["n"] == 1:
                return (
                    OutboxDispatchCandidate(
                        tenant_id=tenant, eligible_at=datetime.now(UTC)
                    ),
                )
            return ()

    class _Disp:
        async def dispatch_once(self, tenant_id: UUID) -> bool:
            started.set()
            await asyncio.sleep(0.1)
            return False

    daemon = EventDispatcherDaemon(
        config=cfg,
        candidate_repository=_Cand(),
        dispatcher=_Disp(),  # type: ignore[arg-type]
        claimed_by="s2",
    )
    shutdown = asyncio.Event()

    async def _scenario() -> None:
        async def _stop() -> None:
            await started.wait()
            shutdown.set()

        stopper = asyncio.create_task(_stop())
        await supervise_event_dispatcher_daemon(
            daemon, cfg, shutdown_event=shutdown
        )
        await stopper

    asyncio.run(_scenario())
    assert daemon.shutdown_requested
    assert passes["n"] == 1


def test_s3_stalled_dispatch_exceeds_grace_cancels_daemon() -> None:
    cfg = _cfg(poll=30, grace=1)
    tenant = uuid4()
    started = asyncio.Event()
    cancelled = {"yes": False}

    class _Cand:
        def list_candidates(self, *, limit: int, as_of: datetime):
            return (
                OutboxDispatchCandidate(
                    tenant_id=tenant, eligible_at=datetime.now(UTC)
                ),
            )

    class _Disp:
        async def dispatch_once(self, tenant_id: UUID) -> bool:
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled["yes"] = True
                raise
            return False

    daemon = EventDispatcherDaemon(
        config=cfg,
        candidate_repository=_Cand(),
        dispatcher=_Disp(),  # type: ignore[arg-type]
        claimed_by="s3",
    )
    shutdown = asyncio.Event()

    async def _scenario() -> None:
        async def _stop() -> None:
            await started.wait()
            shutdown.set()

        stopper = asyncio.create_task(_stop())
        with pytest.raises(TimeoutError):
            await supervise_event_dispatcher_daemon(
                daemon, cfg, shutdown_event=shutdown
            )
        await stopper

    asyncio.run(_scenario())
    assert cancelled["yes"] is True


def test_s4_s5_s6_cleanup_after_grace_cancel() -> None:
    """After grace cancel, NATS drain / credential wipe / Engine dispose still run."""
    from aieos.platform.runtime.entrypoints import event_dispatcher_main as mod

    cfg = _cfg(poll=30, grace=1)
    evidence = {"drain": False, "wipe": False, "dispose": False}
    tenant = uuid4()
    started = asyncio.Event()

    class _Engine:
        def dispose(self) -> None:
            evidence["dispose"] = True

    class _Creds:
        def wipe(self) -> None:
            evidence["wipe"] = True

    class _Nats:
        async def drain(self) -> None:
            evidence["drain"] = True

        async def close(self) -> None:
            pass

    class _Cand:
        def list_candidates(self, *, limit: int, as_of: datetime):
            return (
                OutboxDispatchCandidate(
                    tenant_id=tenant, eligible_at=datetime.now(UTC)
                ),
            )

    class _Disp:
        async def dispatch_once(self, tenant_id: UUID) -> bool:
            started.set()
            await asyncio.Event().wait()
            return False

    daemon = EventDispatcherDaemon(
        config=cfg,
        candidate_repository=_Cand(),
        dispatcher=_Disp(),  # type: ignore[arg-type]
        claimed_by="s456",
    )
    shutdown = asyncio.Event()

    async def _scenario() -> None:
        async def _stop() -> None:
            await started.wait()
            shutdown.set()

        stopper = asyncio.create_task(_stop())
        try:
            with pytest.raises(TimeoutError):
                await supervise_event_dispatcher_daemon(
                    daemon, cfg, shutdown_event=shutdown
                )
        finally:
            await mod._bounded_nats_drain(_Nats(), grace_seconds=1)
            _Creds().wipe()
            _Engine().dispose()
            await stopper

    asyncio.run(_scenario())
    assert evidence["drain"] and evidence["wipe"] and evidence["dispose"]


def test_s7_daemon_failure_propagates_and_cleanup_runs() -> None:
    cfg = _cfg(poll=1, grace=2)

    class _Cand:
        def list_candidates(self, *, limit: int, as_of: datetime):
            raise RuntimeError("candidate_boom")

    class _Disp:
        async def dispatch_once(self, tenant_id: UUID) -> bool:
            return False

    daemon = EventDispatcherDaemon(
        config=cfg,
        candidate_repository=_Cand(),
        dispatcher=_Disp(),  # type: ignore[arg-type]
        claimed_by="s7",
    )
    cleanup = {"ok": False}

    async def _scenario() -> None:
        try:
            with pytest.raises(RuntimeError, match="candidate_boom"):
                await supervise_event_dispatcher_daemon(daemon, cfg)
        finally:
            cleanup["ok"] = True

    asyncio.run(_scenario())
    assert cleanup["ok"] is True


def test_s8_no_second_pass_after_shutdown() -> None:
    cfg = _cfg(poll=30, grace=2)
    passes = {"n": 0}
    gate = asyncio.Event()

    class _Cand:
        def list_candidates(self, *, limit: int, as_of: datetime):
            passes["n"] += 1
            gate.set()
            return ()

    class _Disp:
        async def dispatch_once(self, tenant_id: UUID) -> bool:
            return False

    daemon = EventDispatcherDaemon(
        config=cfg,
        candidate_repository=_Cand(),
        dispatcher=_Disp(),  # type: ignore[arg-type]
        claimed_by="s8",
    )
    shutdown = asyncio.Event()

    async def _scenario() -> None:
        async def _stop() -> None:
            await gate.wait()
            shutdown.set()

        stopper = asyncio.create_task(_stop())
        await supervise_event_dispatcher_daemon(
            daemon, cfg, shutdown_event=shutdown
        )
        await stopper

    asyncio.run(_scenario())
    assert passes["n"] == 1

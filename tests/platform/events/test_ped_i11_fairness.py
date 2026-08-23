"""PED-I11 fairness / daemon scheduling unit tests."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from aieos.platform.events.nats.daemon import (
    EventDispatcherDaemon,
    plan_round_robin,
    run_fair_dispatch_pass,
)
from aieos.platform.events.persistence.candidates import OutboxDispatchCandidate
from aieos.platform.runtime.config_event_dispatcher import EventDispatcherRuntimeConfig
from aieos.platform.runtime.models import DeploymentEnvironment, ReleaseIdentity

pytestmark = pytest.mark.ped_i11


def test_plan_round_robin_interleaves() -> None:
    a, b, c = uuid4(), uuid4(), uuid4()
    schedule = plan_round_robin([a, b, c], max_messages_per_tenant=2)
    assert schedule == [a, b, c, a, b, c]


def test_fair_pass_drops_idle_tenants() -> None:
    a, b, c = uuid4(), uuid4(), uuid4()
    remaining = {a: 2, b: 1, c: 0}

    class _Disp:
        async def dispatch_once(self, tenant_id: UUID) -> bool:
            left = remaining.get(tenant_id, 0)
            if left <= 0:
                return False
            remaining[tenant_id] = left - 1
            return True

    candidates = tuple(
        OutboxDispatchCandidate(tenant_id=t, eligible_at=datetime.now(UTC))
        for t in (a, b, c)
    )
    order: list[UUID] = []

    async def _run() -> None:
        await run_fair_dispatch_pass(
            candidates=candidates,
            dispatcher=_Disp(),
            max_messages_per_tenant=2,
            on_dispatch=lambda tid, ok: order.append(tid),
        )

    asyncio.run(_run())
    assert order == [a, b, c, a, b]


def test_transient_failure_does_not_starve_others() -> None:
    a, b = uuid4(), uuid4()
    calls: list[UUID] = []

    class _Disp:
        async def dispatch_once(self, tenant_id: UUID) -> bool:
            calls.append(tenant_id)
            # A always "fails" (no publish); B succeeds once then idle.
            if tenant_id == a:
                return False
            return calls.count(b) == 1

    candidates = tuple(
        OutboxDispatchCandidate(tenant_id=t, eligible_at=datetime.now(UTC))
        for t in (a, b)
    )

    async def _run() -> None:
        await run_fair_dispatch_pass(
            candidates=candidates,
            dispatcher=_Disp(),
            max_messages_per_tenant=3,
            on_dispatch=None,
        )

    asyncio.run(_run())
    assert calls[0] == a
    assert calls[1] == b
    assert a not in calls[2:]
    # B succeeds once then returns idle on the next round.
    assert calls.count(b) == 2
    assert calls.count(a) == 1


def test_candidate_batch_bound_respected_by_daemon_config_field() -> None:
    release = ReleaseIdentity(
        application_version="0.1.0",
        git_sha="a" * 40,
        build_id="b1",
        artifact_digest="sha256:" + ("c" * 64),
    )
    cfg = EventDispatcherRuntimeConfig(
        environment=DeploymentEnvironment.PRODUCTION,
        release_identity=release,
        database_url="postgresql+psycopg://u:p@localhost/db",
        database_role="aieos_event_dispatcher",
        database_connect_timeout_seconds=5,
        nats_url="tls://nats.example:4222",
        nats_credentials="x",
        nats_connect_timeout_seconds=5,
        nats_ca_bundle_path=None,
        poll_interval_seconds=1,
        candidate_batch_size=2,
        max_messages_per_tenant_per_pass=1,
        claim_lease_seconds=30,
        max_attempts=3,
        retry_delay_seconds=1,
        publish_timeout_seconds=5,
        shutdown_grace_seconds=5,
    )
    seen_limits: list[int] = []

    class _Cand:
        def list_candidates(self, *, limit: int, as_of: datetime):
            seen_limits.append(limit)
            return ()

    class _Disp:
        async def dispatch_once(self, tenant_id: UUID) -> bool:
            return False

    daemon = EventDispatcherDaemon(
        config=cfg,
        candidate_repository=_Cand(),
        dispatcher=_Disp(),  # type: ignore[arg-type]
        claimed_by="test",
    )
    asyncio.run(daemon.run_once())
    assert seen_limits == [2]


def test_shutdown_wakes_poll_wait() -> None:
    release = ReleaseIdentity(
        application_version="0.1.0",
        git_sha="a" * 40,
        build_id="b1",
        artifact_digest="sha256:" + ("c" * 64),
    )
    cfg = EventDispatcherRuntimeConfig(
        environment=DeploymentEnvironment.PRODUCTION,
        release_identity=release,
        database_url="postgresql+psycopg://u:p@localhost/db",
        database_role="aieos_event_dispatcher",
        database_connect_timeout_seconds=5,
        nats_url="tls://nats.example:4222",
        nats_credentials="x",
        nats_connect_timeout_seconds=5,
        nats_ca_bundle_path=None,
        poll_interval_seconds=30,
        candidate_batch_size=10,
        max_messages_per_tenant_per_pass=1,
        claim_lease_seconds=30,
        max_attempts=3,
        retry_delay_seconds=1,
        publish_timeout_seconds=5,
        shutdown_grace_seconds=5,
    )

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
        claimed_by="test",
    )

    async def _scenario() -> None:
        async def _stop_soon() -> None:
            await asyncio.sleep(0.05)
            daemon.request_shutdown()

        stopper = asyncio.create_task(_stop_soon())
        await asyncio.wait_for(daemon.run_forever(), timeout=2.0)
        await stopper

    asyncio.run(_scenario())
    assert daemon.shutdown_requested

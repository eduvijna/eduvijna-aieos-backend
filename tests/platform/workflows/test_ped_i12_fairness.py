"""PED-I12 fairness / dual-stream daemon scheduling unit tests."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from aieos.platform.runtime.config_workflow_dispatcher import (
    WorkflowDispatcherRuntimeConfig,
)
from aieos.platform.runtime.models import DeploymentEnvironment, ReleaseIdentity
from aieos.platform.workflows.persistence.candidates import WorkflowDispatchCandidate
from aieos.platform.workflows.temporal.daemon import (
    WorkflowDispatcherDaemon,
    plan_round_robin,
    run_fair_dispatch_pass,
)

pytestmark = pytest.mark.ped_i12


def _cfg(
    *,
    poll: int = 1,
    batch: int = 10,
    max_per: int = 1,
    grace: int = 5,
) -> WorkflowDispatcherRuntimeConfig:
    return WorkflowDispatcherRuntimeConfig(
        environment=DeploymentEnvironment.PRODUCTION,
        release_identity=ReleaseIdentity(
            application_version="0.1.0",
            git_sha="a" * 40,
            build_id="b1",
            artifact_digest="sha256:" + ("c" * 64),
        ),
        database_url="postgresql+psycopg://u:p@localhost/db",
        database_role="aieos_workflow_dispatcher",
        database_connect_timeout_seconds=5,
        temporal_target_host="temporal.example:7233",
        temporal_namespace="ns",
        temporal_api_key="x",
        temporal_connect_timeout_seconds=5,
        poll_interval_seconds=poll,
        candidate_batch_size=batch,
        max_intents_per_tenant_per_pass=max_per,
        claim_lease_seconds=30,
        max_attempts=3,
        retry_delay_seconds=1,
        result_timeout_seconds=30,
        start_reconciliation_timeout_seconds=10,
        shutdown_grace_seconds=grace,
    )


def test_plan_round_robin_interleaves() -> None:
    a, b, c = uuid4(), uuid4(), uuid4()
    schedule = plan_round_robin([a, b, c], max_intents_per_tenant=2)
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
        WorkflowDispatchCandidate(tenant_id=t, eligible_at=datetime.now(UTC))
        for t in (a, b, c)
    )
    order: list[UUID] = []

    async def _run() -> None:
        await run_fair_dispatch_pass(
            candidates=candidates,
            dispatcher=_Disp(),
            max_intents_per_tenant=2,
            kind="START",
            on_dispatch=lambda _k, tid, _ok: order.append(tid),
        )

    asyncio.run(_run())
    assert order == [a, b, c, a, b]


def test_transient_failure_does_not_hot_spin_or_starve() -> None:
    a, b = uuid4(), uuid4()
    calls: list[UUID] = []

    class _Disp:
        async def dispatch_once(self, tenant_id: UUID) -> bool:
            calls.append(tenant_id)
            if tenant_id == a:
                return False
            return calls.count(b) == 1

    candidates = tuple(
        WorkflowDispatchCandidate(tenant_id=t, eligible_at=datetime.now(UTC))
        for t in (a, b)
    )

    async def _run() -> None:
        await run_fair_dispatch_pass(
            candidates=candidates,
            dispatcher=_Disp(),
            max_intents_per_tenant=3,
            kind="COMMAND",
            on_dispatch=None,
        )

    asyncio.run(_run())
    assert calls.count(a) == 1
    assert calls.count(b) == 2


def test_zero_candidates() -> None:
    cfg = _cfg()

    class _Empty:
        def list_candidates(self, *, limit: int, as_of: datetime):
            return ()

    class _Disp:
        async def dispatch_once(self, tenant_id: UUID) -> bool:
            raise AssertionError("must not dispatch")

    daemon = WorkflowDispatcherDaemon(
        config=cfg,
        start_candidate_repository=_Empty(),
        command_candidate_repository=_Empty(),
        start_dispatcher=_Disp(),  # type: ignore[arg-type]
        command_dispatcher=_Disp(),  # type: ignore[arg-type]
        claimed_by="test",
    )
    stats = asyncio.run(daemon.run_once())
    assert stats.start.candidate_count == 0
    assert stats.command.candidate_count == 0
    assert stats.start.dispatches_attempted == 0
    assert stats.command.dispatches_attempted == 0


def test_start_only_and_command_only() -> None:
    start_t = uuid4()
    command_t = uuid4()
    start_calls: list[UUID] = []
    command_calls: list[UUID] = []

    class _StartCand:
        def list_candidates(self, *, limit: int, as_of: datetime):
            return (
                WorkflowDispatchCandidate(
                    tenant_id=start_t, eligible_at=datetime.now(UTC)
                ),
            )

    class _Empty:
        def list_candidates(self, *, limit: int, as_of: datetime):
            return ()

    class _CommandCand:
        def list_candidates(self, *, limit: int, as_of: datetime):
            return (
                WorkflowDispatchCandidate(
                    tenant_id=command_t, eligible_at=datetime.now(UTC)
                ),
            )

    class _StartDisp:
        async def dispatch_once(self, tenant_id: UUID) -> bool:
            start_calls.append(tenant_id)
            return False

    class _CommandDisp:
        async def dispatch_once(self, tenant_id: UUID) -> bool:
            command_calls.append(tenant_id)
            return False

    daemon = WorkflowDispatcherDaemon(
        config=_cfg(),
        start_candidate_repository=_StartCand(),
        command_candidate_repository=_Empty(),
        start_dispatcher=_StartDisp(),  # type: ignore[arg-type]
        command_dispatcher=_CommandDisp(),  # type: ignore[arg-type]
        claimed_by="start-only",
    )
    asyncio.run(daemon.run_once())
    assert start_calls == [start_t]
    assert command_calls == []

    daemon2 = WorkflowDispatcherDaemon(
        config=_cfg(),
        start_candidate_repository=_Empty(),
        command_candidate_repository=_CommandCand(),
        start_dispatcher=_StartDisp(),  # type: ignore[arg-type]
        command_dispatcher=_CommandDisp(),  # type: ignore[arg-type]
        claimed_by="command-only",
    )
    asyncio.run(daemon2.run_once())
    assert command_calls == [command_t]


def test_mixed_streams_both_progress_and_alternate_priority() -> None:
    start_t = uuid4()
    command_t = uuid4()
    order: list[str] = []

    class _StartCand:
        def list_candidates(self, *, limit: int, as_of: datetime):
            return (
                WorkflowDispatchCandidate(
                    tenant_id=start_t, eligible_at=datetime.now(UTC)
                ),
            )

    class _CommandCand:
        def list_candidates(self, *, limit: int, as_of: datetime):
            return (
                WorkflowDispatchCandidate(
                    tenant_id=command_t, eligible_at=datetime.now(UTC)
                ),
            )

    class _StartDisp:
        async def dispatch_once(self, tenant_id: UUID) -> bool:
            order.append("START")
            return False

    class _CommandDisp:
        async def dispatch_once(self, tenant_id: UUID) -> bool:
            order.append("COMMAND")
            return False

    daemon = WorkflowDispatcherDaemon(
        config=_cfg(),
        start_candidate_repository=_StartCand(),
        command_candidate_repository=_CommandCand(),
        start_dispatcher=_StartDisp(),  # type: ignore[arg-type]
        command_dispatcher=_CommandDisp(),  # type: ignore[arg-type]
        claimed_by="mixed",
    )
    s1 = asyncio.run(daemon.run_once())
    s2 = asyncio.run(daemon.run_once())
    assert s1.first_stream == "START"
    assert s2.first_stream == "COMMAND"
    assert order == ["START", "COMMAND", "COMMAND", "START"]


def test_noisy_tenant_does_not_starve_others() -> None:
    noisy = uuid4()
    quiet = uuid4()
    remaining = {noisy: 100, quiet: 1}
    seen: list[UUID] = []

    class _Disp:
        async def dispatch_once(self, tenant_id: UUID) -> bool:
            seen.append(tenant_id)
            left = remaining.get(tenant_id, 0)
            if left <= 0:
                return False
            remaining[tenant_id] = left - 1
            return True

    candidates = tuple(
        WorkflowDispatchCandidate(tenant_id=t, eligible_at=datetime.now(UTC))
        for t in (noisy, quiet)
    )

    async def _run() -> None:
        await run_fair_dispatch_pass(
            candidates=candidates,
            dispatcher=_Disp(),
            max_intents_per_tenant=2,
            kind="START",
            on_dispatch=None,
        )

    asyncio.run(_run())
    # Quiet tenant receives progress opportunity; noisy tenant is bounded by
    # max_intents_per_tenant (no exclusive starvation of quiet).
    assert quiet in seen
    assert seen.count(noisy) == 2
    assert seen.index(quiet) < len(seen)


def test_candidate_batch_bound_respected() -> None:
    seen_limits: list[tuple[str, int]] = []

    class _Cand:
        def __init__(self, kind: str) -> None:
            self.kind = kind

        def list_candidates(self, *, limit: int, as_of: datetime):
            seen_limits.append((self.kind, limit))
            return ()

    class _Disp:
        async def dispatch_once(self, tenant_id: UUID) -> bool:
            return False

    daemon = WorkflowDispatcherDaemon(
        config=_cfg(batch=2),
        start_candidate_repository=_Cand("START"),
        command_candidate_repository=_Cand("COMMAND"),
        start_dispatcher=_Disp(),  # type: ignore[arg-type]
        command_dispatcher=_Disp(),  # type: ignore[arg-type]
        claimed_by="batch",
    )
    asyncio.run(daemon.run_once())
    assert seen_limits == [("START", 2), ("COMMAND", 2)]


def test_shutdown_wakes_poll_wait() -> None:
    class _Cand:
        def list_candidates(self, *, limit: int, as_of: datetime):
            return ()

    class _Disp:
        async def dispatch_once(self, tenant_id: UUID) -> bool:
            return False

    daemon = WorkflowDispatcherDaemon(
        config=_cfg(poll=30),
        start_candidate_repository=_Cand(),
        command_candidate_repository=_Cand(),
        start_dispatcher=_Disp(),  # type: ignore[arg-type]
        command_dispatcher=_Disp(),  # type: ignore[arg-type]
        claimed_by="shutdown",
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

"""PED-I12 shutdown-grace supervision tests."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from aieos.platform.runtime.config_workflow_dispatcher import (
    WorkflowDispatcherRuntimeConfig,
)
from aieos.platform.runtime.entrypoints.workflow_dispatcher_main import (
    supervise_workflow_dispatcher_daemon,
)
from aieos.platform.runtime.models import DeploymentEnvironment, ReleaseIdentity
from aieos.platform.workflows.persistence.candidates import WorkflowDispatchCandidate
from aieos.platform.workflows.temporal.daemon import WorkflowDispatcherDaemon

pytestmark = pytest.mark.ped_i12


def _cfg(*, poll: int = 30, grace: int = 2) -> WorkflowDispatcherRuntimeConfig:
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
        candidate_batch_size=10,
        max_intents_per_tenant_per_pass=1,
        claim_lease_seconds=30,
        max_attempts=3,
        retry_delay_seconds=1,
        result_timeout_seconds=30,
        start_reconciliation_timeout_seconds=10,
        shutdown_grace_seconds=grace,
    )


def _daemon(
    cfg: WorkflowDispatcherRuntimeConfig,
    *,
    start_cand=None,
    command_cand=None,
    start_disp=None,
    command_disp=None,
    claimed_by: str = "test",
) -> WorkflowDispatcherDaemon:
    class _Empty:
        def list_candidates(self, *, limit: int, as_of: datetime):
            return ()

    class _Idle:
        async def dispatch_once(self, tenant_id: UUID) -> bool:
            return False

    return WorkflowDispatcherDaemon(
        config=cfg,
        start_candidate_repository=start_cand or _Empty(),
        command_candidate_repository=command_cand or _Empty(),
        start_dispatcher=start_disp or _Idle(),  # type: ignore[arg-type]
        command_dispatcher=command_disp or _Idle(),  # type: ignore[arg-type]
        claimed_by=claimed_by,
    )


def test_shutdown_before_pass_wakes_poll() -> None:
    cfg = _cfg(poll=30, grace=2)
    daemon = _daemon(cfg, claimed_by="before")
    shutdown = asyncio.Event()

    async def _scenario() -> None:
        async def _stop() -> None:
            await asyncio.sleep(0.05)
            shutdown.set()

        stopper = asyncio.create_task(_stop())
        await asyncio.wait_for(
            supervise_workflow_dispatcher_daemon(daemon, cfg, shutdown_event=shutdown),
            timeout=2.0,
        )
        await stopper

    asyncio.run(_scenario())
    assert daemon.shutdown_requested


def test_shutdown_during_pass_completes_inside_grace() -> None:
    cfg = _cfg(poll=30, grace=2)
    tenant = uuid4()
    started = asyncio.Event()
    passes = {"n": 0}

    class _Cand:
        def list_candidates(self, *, limit: int, as_of: datetime):
            passes["n"] += 1
            if passes["n"] == 1:
                return (
                    WorkflowDispatchCandidate(
                        tenant_id=tenant, eligible_at=datetime.now(UTC)
                    ),
                )
            return ()

    class _Disp:
        async def dispatch_once(self, tenant_id: UUID) -> bool:
            started.set()
            await asyncio.sleep(0.1)
            return False

    daemon = _daemon(
        cfg,
        start_cand=_Cand(),
        start_disp=_Disp(),
        claimed_by="during",
    )
    shutdown = asyncio.Event()

    async def _scenario() -> None:
        async def _stop() -> None:
            await started.wait()
            shutdown.set()

        stopper = asyncio.create_task(_stop())
        await supervise_workflow_dispatcher_daemon(
            daemon, cfg, shutdown_event=shutdown
        )
        await stopper

    asyncio.run(_scenario())
    assert daemon.shutdown_requested
    assert passes["n"] == 1


def test_shutdown_grace_expiry_cancels() -> None:
    cfg = _cfg(poll=30, grace=1)
    tenant = uuid4()
    started = asyncio.Event()
    cancelled = {"yes": False}

    class _Cand:
        def list_candidates(self, *, limit: int, as_of: datetime):
            return (
                WorkflowDispatchCandidate(
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

    daemon = _daemon(
        cfg,
        start_cand=_Cand(),
        start_disp=_Disp(),
        claimed_by="grace",
    )
    shutdown = asyncio.Event()

    async def _scenario() -> None:
        async def _stop() -> None:
            await started.wait()
            shutdown.set()

        stopper = asyncio.create_task(_stop())
        with pytest.raises(TimeoutError):
            await supervise_workflow_dispatcher_daemon(
                daemon, cfg, shutdown_event=shutdown
            )
        await stopper

    asyncio.run(_scenario())
    assert cancelled["yes"] is True


def test_no_second_pass_after_shutdown() -> None:
    cfg = _cfg(poll=30, grace=2)
    passes = {"n": 0}
    gate = asyncio.Event()

    class _Cand:
        def list_candidates(self, *, limit: int, as_of: datetime):
            passes["n"] += 1
            gate.set()
            return ()

    daemon = _daemon(cfg, start_cand=_Cand(), claimed_by="once")
    shutdown = asyncio.Event()

    async def _scenario() -> None:
        async def _stop() -> None:
            await gate.wait()
            shutdown.set()

        stopper = asyncio.create_task(_stop())
        await supervise_workflow_dispatcher_daemon(
            daemon, cfg, shutdown_event=shutdown
        )
        await stopper

    asyncio.run(_scenario())
    assert passes["n"] == 1

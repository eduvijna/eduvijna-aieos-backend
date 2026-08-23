"""Bounded WORKFLOW dispatcher daemon scheduling (PED-I12).

Not a general scheduler/reconciliation subsystem. Fairness is round-robin across
ADR-AIEOS-045 START and COMMAND candidates with bounded work per tenant. Both
streams receive progress opportunity every pass (START then COMMAND), with
stream order alternating each pass so neither indefinitely suppresses the other.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Awaitable, Callable, Protocol
from uuid import UUID

from aieos.platform.runtime.config_workflow_dispatcher import (
    WorkflowDispatcherRuntimeConfig,
)
from aieos.platform.runtime.models import WorkloadKind
from aieos.platform.workflows.persistence.candidates import WorkflowDispatchCandidate
from aieos.platform.workflows.temporal.dispatchers import (
    ContentReviewCommandDispatcher,
    ContentReviewStartDispatcher,
    DispatcherConfig,
)

logger = logging.getLogger(__name__)


class CandidateLister(Protocol):
    def list_candidates(
        self,
        *,
        limit: int,
        as_of: datetime,
    ) -> tuple[WorkflowDispatchCandidate, ...]: ...


class TenantDispatcher(Protocol):
    async def dispatch_once(self, tenant_id: UUID) -> bool: ...


def build_claimed_by(config: WorkflowDispatcherRuntimeConfig) -> str:
    return (
        f"aieos.workflow-dispatcher/{config.release_identity.build_id}/"
        f"{uuid.uuid4()}"
    )


@dataclass(frozen=True, slots=True)
class StreamPassStats:
    kind: str
    candidate_count: int
    dispatches_attempted: int
    dispatches_delivered: int


@dataclass(frozen=True, slots=True)
class DispatchPassStats:
    start: StreamPassStats
    command: StreamPassStats
    first_stream: str


def plan_round_robin(
    tenant_ids: list[UUID],
    *,
    max_intents_per_tenant: int,
) -> list[UUID]:
    """Deterministic round-robin schedule: A,B,C,A,B,C… up to max per tenant."""
    if max_intents_per_tenant < 1:
        raise ValueError("max_intents_per_tenant must be >= 1")
    schedule: list[UUID] = []
    for _ in range(max_intents_per_tenant):
        schedule.extend(tenant_ids)
    return schedule


async def run_fair_dispatch_pass(
    *,
    candidates: tuple[WorkflowDispatchCandidate, ...],
    dispatcher: TenantDispatcher,
    max_intents_per_tenant: int,
    kind: str,
    on_dispatch: Callable[[str, UUID, bool], None] | None = None,
) -> StreamPassStats:
    """Round-robin dispatch; tenants that return no work leave remaining rounds."""
    active = [c.tenant_id for c in candidates]
    attempted = 0
    delivered = 0
    for _round in range(max_intents_per_tenant):
        if not active:
            break
        next_active: list[UUID] = []
        for tenant_id in active:
            did_work = await dispatcher.dispatch_once(tenant_id)
            attempted += 1
            if on_dispatch is not None:
                on_dispatch(kind, tenant_id, did_work)
            if did_work:
                delivered += 1
                next_active.append(tenant_id)
            # No claim / failure → drop from later rounds (fairness for others).
        active = next_active
    return StreamPassStats(
        kind=kind,
        candidate_count=len(candidates),
        dispatches_attempted=attempted,
        dispatches_delivered=delivered,
    )


class WorkflowDispatcherDaemon:
    """WORKFLOW-only daemon: START + COMMAND candidates → fair dispatch → poll."""

    def __init__(
        self,
        *,
        config: WorkflowDispatcherRuntimeConfig,
        start_candidate_repository: CandidateLister,
        command_candidate_repository: CandidateLister,
        start_dispatcher: ContentReviewStartDispatcher,
        command_dispatcher: ContentReviewCommandDispatcher,
        claimed_by: str,
        clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._config = config
        self._start_candidates = start_candidate_repository
        self._command_candidates = command_candidate_repository
        self._start_dispatcher = start_dispatcher
        self._command_dispatcher = command_dispatcher
        self._claimed_by = claimed_by
        self._clock = clock or (lambda: datetime.now(UTC))
        self._sleep = sleep or asyncio.sleep
        self._shutdown = asyncio.Event()
        self._pass_index = 0

    def request_shutdown(self) -> None:
        self._shutdown.set()

    @property
    def shutdown_requested(self) -> bool:
        return self._shutdown.is_set()

    @property
    def claimed_by(self) -> str:
        return self._claimed_by

    async def _wait_poll_or_shutdown(self) -> None:
        try:
            await asyncio.wait_for(
                self._shutdown.wait(),
                timeout=float(self._config.poll_interval_seconds),
            )
        except TimeoutError:
            return

    async def run_once(self) -> DispatchPassStats:
        as_of = self._clock()
        # Alternate which stream runs first so neither permanently suppresses
        # the other under heavy load.
        first_start = self._pass_index % 2 == 0
        self._pass_index += 1
        first_stream = "START" if first_start else "COMMAND"

        logger.info(
            "workflow_dispatcher candidate_invocation workload=%s git_sha=%s "
            "claimed_by=%s first_stream=%s",
            WorkloadKind.WORKFLOW_DISPATCHER.value,
            self._config.release_identity.git_sha,
            self._claimed_by,
            first_stream,
        )

        start_candidates = self._start_candidates.list_candidates(
            limit=self._config.candidate_batch_size,
            as_of=as_of,
        )
        command_candidates = self._command_candidates.list_candidates(
            limit=self._config.candidate_batch_size,
            as_of=as_of,
        )
        logger.info(
            "workflow_dispatcher candidate_count start=%s command=%s workload=%s",
            len(start_candidates),
            len(command_candidates),
            WorkloadKind.WORKFLOW_DISPATCHER.value,
        )

        def _on_dispatch(kind: str, tenant_id: UUID, delivered: bool) -> None:
            logger.info(
                "workflow_dispatcher tenant_pass kind=%s tenant_id=%s delivered=%s "
                "claimed_by=%s",
                kind,
                tenant_id,
                delivered,
                self._claimed_by,
            )

        async def _run_start() -> StreamPassStats:
            return await run_fair_dispatch_pass(
                candidates=start_candidates,
                dispatcher=self._start_dispatcher,
                max_intents_per_tenant=self._config.max_intents_per_tenant_per_pass,
                kind="START",
                on_dispatch=_on_dispatch,
            )

        async def _run_command() -> StreamPassStats:
            return await run_fair_dispatch_pass(
                candidates=command_candidates,
                dispatcher=self._command_dispatcher,
                max_intents_per_tenant=self._config.max_intents_per_tenant_per_pass,
                kind="COMMAND",
                on_dispatch=_on_dispatch,
            )

        if first_start:
            start_stats = await _run_start()
            command_stats = await _run_command()
        else:
            command_stats = await _run_command()
            start_stats = await _run_start()

        stats = DispatchPassStats(
            start=start_stats,
            command=command_stats,
            first_stream=first_stream,
        )
        logger.info(
            "workflow_dispatcher pass_complete first_stream=%s "
            "start_attempted=%s start_delivered=%s start_candidates=%s "
            "command_attempted=%s command_delivered=%s command_candidates=%s",
            stats.first_stream,
            stats.start.dispatches_attempted,
            stats.start.dispatches_delivered,
            stats.start.candidate_count,
            stats.command.dispatches_attempted,
            stats.command.dispatches_delivered,
            stats.command.candidate_count,
        )
        return stats

    async def run_forever(self) -> None:
        while not self._shutdown.is_set():
            await self.run_once()
            if self._shutdown.is_set():
                break
            await self._wait_poll_or_shutdown()
        logger.info(
            "workflow_dispatcher shutdown_completed claimed_by=%s git_sha=%s",
            self._claimed_by,
            self._config.release_identity.git_sha,
        )


def dispatcher_config_from_runtime(
    config: WorkflowDispatcherRuntimeConfig,
    *,
    claimed_by: str,
) -> DispatcherConfig:
    return DispatcherConfig(
        claim_lease=timedelta(seconds=config.claim_lease_seconds),
        max_attempts=config.max_attempts,
        retry_delay=timedelta(seconds=config.retry_delay_seconds),
        claimed_by=claimed_by,
        result_timeout_seconds=float(config.result_timeout_seconds),
        start_reconciliation_timeout_seconds=float(
            config.start_reconciliation_timeout_seconds
        ),
    )

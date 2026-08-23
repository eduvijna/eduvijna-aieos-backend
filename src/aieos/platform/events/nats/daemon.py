"""Bounded EVENT dispatcher daemon scheduling (PED-I11).

Not a general scheduler/reconciliation subsystem. Fairness is round-robin across
ADR-AIEOS-045 candidates with bounded work per tenant.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Awaitable, Callable, Protocol
from uuid import UUID

from aieos.platform.events.nats.dispatcher import ContentOutboxDispatcher
from aieos.platform.events.persistence.candidates import OutboxDispatchCandidate
from aieos.platform.runtime.config_event_dispatcher import EventDispatcherRuntimeConfig
from aieos.platform.runtime.models import WorkloadKind

logger = logging.getLogger(__name__)


class CandidateLister(Protocol):
    def list_candidates(
        self,
        *,
        limit: int,
        as_of: datetime,
    ) -> tuple[OutboxDispatchCandidate, ...]: ...


class TenantDispatcher(Protocol):
    async def dispatch_once(self, tenant_id: UUID) -> bool: ...


def build_claimed_by(config: EventDispatcherRuntimeConfig) -> str:
    return (
        f"aieos.event-dispatcher/{config.release_identity.build_id}/"
        f"{uuid.uuid4()}"
    )


@dataclass(frozen=True, slots=True)
class DispatchPassStats:
    candidate_count: int
    dispatches_attempted: int
    dispatches_published: int


def plan_round_robin(
    tenant_ids: list[UUID],
    *,
    max_messages_per_tenant: int,
) -> list[UUID]:
    """Deterministic round-robin schedule: A,B,C,A,B,C… up to max per tenant."""
    if max_messages_per_tenant < 1:
        raise ValueError("max_messages_per_tenant must be >= 1")
    schedule: list[UUID] = []
    for _ in range(max_messages_per_tenant):
        schedule.extend(tenant_ids)
    return schedule


async def run_fair_dispatch_pass(
    *,
    candidates: tuple[OutboxDispatchCandidate, ...],
    dispatcher: TenantDispatcher,
    max_messages_per_tenant: int,
    on_dispatch: Callable[[UUID, bool], None] | None = None,
) -> DispatchPassStats:
    """Round-robin dispatch; tenants that return no work leave remaining rounds."""
    active = [c.tenant_id for c in candidates]
    attempted = 0
    published = 0
    for _round in range(max_messages_per_tenant):
        if not active:
            break
        next_active: list[UUID] = []
        for tenant_id in active:
            did_work = await dispatcher.dispatch_once(tenant_id)
            attempted += 1
            if on_dispatch is not None:
                on_dispatch(tenant_id, did_work)
            if did_work:
                published += 1
                next_active.append(tenant_id)
            # No claim / failure → drop from later rounds (fairness for others).
        active = next_active
    return DispatchPassStats(
        candidate_count=len(candidates),
        dispatches_attempted=attempted,
        dispatches_published=published,
    )


class EventDispatcherDaemon:
    """EVENT-only daemon loop: candidates → fair dispatch_once → poll wait."""

    def __init__(
        self,
        *,
        config: EventDispatcherRuntimeConfig,
        candidate_repository: CandidateLister,
        dispatcher: ContentOutboxDispatcher,
        claimed_by: str,
        clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._config = config
        self._candidates = candidate_repository
        self._dispatcher = dispatcher
        self._claimed_by = claimed_by
        self._clock = clock or (lambda: datetime.now(UTC))
        self._sleep = sleep or asyncio.sleep
        self._shutdown = asyncio.Event()

    def request_shutdown(self) -> None:
        self._shutdown.set()

    @property
    def shutdown_requested(self) -> bool:
        return self._shutdown.is_set()

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
        logger.info(
            "event_dispatcher candidate_invocation workload=%s git_sha=%s claimed_by=%s",
            WorkloadKind.EVENT_DISPATCHER.value,
            self._config.release_identity.git_sha,
            self._claimed_by,
        )
        candidates = self._candidates.list_candidates(
            limit=self._config.candidate_batch_size,
            as_of=as_of,
        )
        logger.info(
            "event_dispatcher candidate_count=%s workload=%s",
            len(candidates),
            WorkloadKind.EVENT_DISPATCHER.value,
        )

        def _on_dispatch(tenant_id: UUID, published: bool) -> None:
            logger.info(
                "event_dispatcher tenant_pass tenant_id=%s published=%s claimed_by=%s",
                tenant_id,
                published,
                self._claimed_by,
            )

        # Prefer detailed outcomes for operational evidence when available.
        detailed = getattr(self._dispatcher, "dispatch_once_detailed", None)
        if detailed is not None:

            class _DetailedAdapter:
                async def dispatch_once(self_inner, tenant_id: UUID) -> bool:
                    outcome = await detailed(tenant_id)
                    logger.info(
                        "event_dispatcher tenant_outcome tenant_id=%s claimed=%s "
                        "published=%s event_id=%s attempt_count=%s error_code=%s "
                        "broker_stream=%s broker_sequence=%s quarantine=%s "
                        "fence_finalized=%s claimed_by=%s",
                        tenant_id,
                        outcome.claimed,
                        outcome.published,
                        outcome.event_id,
                        outcome.attempt_count,
                        outcome.error_code,
                        outcome.broker_stream,
                        outcome.broker_sequence,
                        outcome.quarantined,
                        outcome.fence_finalized,
                        self._claimed_by,
                    )
                    return outcome.published

            tenant_dispatcher: TenantDispatcher = _DetailedAdapter()
        else:
            tenant_dispatcher = self._dispatcher

        stats = await run_fair_dispatch_pass(
            candidates=candidates,
            dispatcher=tenant_dispatcher,
            max_messages_per_tenant=self._config.max_messages_per_tenant_per_pass,
            on_dispatch=_on_dispatch,
        )
        logger.info(
            "event_dispatcher pass_complete attempted=%s published=%s candidates=%s",
            stats.dispatches_attempted,
            stats.dispatches_published,
            stats.candidate_count,
        )
        return stats

    async def run_forever(self) -> None:
        while not self._shutdown.is_set():
            await self.run_once()
            if self._shutdown.is_set():
                break
            await self._wait_poll_or_shutdown()
        logger.info(
            "event_dispatcher shutdown_completed claimed_by=%s git_sha=%s",
            self._claimed_by,
            self._config.release_identity.git_sha,
        )


def outbox_config_from_runtime(
    config: EventDispatcherRuntimeConfig,
    *,
    claimed_by: str,
) -> "object":
    from aieos.platform.events.nats.dispatcher import OutboxDispatcherConfig

    return OutboxDispatcherConfig(
        claim_lease=timedelta(seconds=config.claim_lease_seconds),
        max_attempts=config.max_attempts,
        retry_delay=timedelta(seconds=config.retry_delay_seconds),
        claimed_by=claimed_by,
        publish_timeout_seconds=float(config.publish_timeout_seconds),
    )

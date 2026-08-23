"""Tenant-scoped outbox dispatch-once publisher."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Callable
from uuid import UUID

from aieos.platform.events.constants import ERROR_NATS_UNAVAILABLE, ERROR_RETRY_EXHAUSTED
from aieos.platform.events.nats.publisher import EventPublisher, PublishResult
from aieos.platform.events.persistence.repositories import (
    SqlAlchemyOutboxDispatcherRepository,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class OutboxDispatcherConfig:
    claim_lease: timedelta
    max_attempts: int
    retry_delay: timedelta
    claimed_by: str
    publish_timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        if self.claim_lease.total_seconds() <= 0:
            raise ValueError("claim_lease must be positive")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if self.retry_delay.total_seconds() < 0:
            raise ValueError("retry_delay must be non-negative")
        if not self.claimed_by.strip():
            raise ValueError("claimed_by must be non-empty")
        if self.publish_timeout_seconds <= 0:
            raise ValueError("publish_timeout_seconds must be positive")


@dataclass(frozen=True, slots=True)
class DispatchOnceOutcome:
    claimed: bool
    published: bool
    event_id: UUID | None = None
    attempt_count: int | None = None
    error_code: str | None = None
    broker_stream: str | None = None
    broker_sequence: int | None = None
    quarantined: bool = False
    fence_finalized: bool | None = None


class ContentOutboxDispatcher:
    def __init__(
        self,
        repository: SqlAlchemyOutboxDispatcherRepository,
        publisher: EventPublisher,
        config: OutboxDispatcherConfig,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._publisher = publisher
        self._config = config
        self._clock = clock or (lambda: datetime.now(UTC))

    async def dispatch_once(self, tenant_id: UUID) -> bool:
        outcome = await self.dispatch_once_detailed(tenant_id)
        return outcome.published

    async def dispatch_once_detailed(self, tenant_id: UUID) -> DispatchOnceOutcome:
        now = self._clock()
        claimed = self._repository.claim_once(
            tenant_id=tenant_id,
            claimed_by=self._config.claimed_by,
            now=now,
            claim_until=now + self._config.claim_lease,
        )
        if claimed is None:
            logger.info(
                "event_dispatcher claim_none tenant_id=%s claimed_by=%s",
                tenant_id,
                self._config.claimed_by,
            )
            return DispatchOnceOutcome(claimed=False, published=False)
        fence_by = claimed.claimed_by or self._config.claimed_by
        fence_attempt = claimed.attempt_count
        event_id = claimed.event_id.value
        logger.info(
            "event_dispatcher claim_found tenant_id=%s event_id=%s attempt_count=%s claimed_by=%s",
            tenant_id,
            event_id,
            fence_attempt,
            fence_by,
        )
        try:
            async with asyncio.timeout(self._config.publish_timeout_seconds):
                result = await self._publisher.publish(claimed)
        except TimeoutError:
            result = PublishResult(
                published=False,
                error_code=ERROR_NATS_UNAVAILABLE,
                permanent=False,
            )
        if result.published and result.ack is not None:
            finalized = self._repository.mark_published(
                tenant_id=tenant_id,
                event_id=event_id,
                claimed_by=fence_by,
                attempt_count=fence_attempt,
                published_at=self._clock(),
                broker_stream=result.ack.stream,
                broker_sequence=result.ack.sequence,
            )
            logger.info(
                "event_dispatcher publish_success tenant_id=%s event_id=%s "
                "broker_stream=%s broker_sequence=%s fence_finalized=%s",
                tenant_id,
                event_id,
                result.ack.stream,
                result.ack.sequence,
                finalized,
            )
            return DispatchOnceOutcome(
                claimed=True,
                published=bool(finalized),
                event_id=event_id,
                attempt_count=fence_attempt,
                broker_stream=result.ack.stream,
                broker_sequence=result.ack.sequence,
                fence_finalized=bool(finalized),
            )
        quarantine = (
            result.permanent or claimed.attempt_count >= self._config.max_attempts
        )
        error_code = (
            ERROR_RETRY_EXHAUSTED
            if (
                not result.permanent
                and claimed.attempt_count >= self._config.max_attempts
            )
            else (result.error_code or ERROR_RETRY_EXHAUSTED)
        )
        if result.ack is not None and result.error_code:
            logger.info(
                "event_dispatcher publish_failure tenant_id=%s event_id=%s "
                "error_code=%s expected_or_actual_stream=%s broker_sequence=%s quarantine=%s",
                tenant_id,
                event_id,
                error_code,
                result.ack.stream,
                result.ack.sequence,
                quarantine,
            )
        else:
            logger.info(
                "event_dispatcher publish_failure tenant_id=%s event_id=%s "
                "error_code=%s quarantine=%s",
                tenant_id,
                event_id,
                error_code,
                quarantine,
            )
        finalized = self._repository.release_for_retry(
            tenant_id=tenant_id,
            event_id=event_id,
            claimed_by=fence_by,
            attempt_count=fence_attempt,
            available_at=self._clock() + self._config.retry_delay,
            error_code=error_code,
            quarantine=quarantine,
        )
        return DispatchOnceOutcome(
            claimed=True,
            published=False,
            event_id=event_id,
            attempt_count=fence_attempt,
            error_code=error_code,
            broker_stream=result.ack.stream if result.ack else None,
            broker_sequence=result.ack.sequence if result.ack else None,
            quarantined=quarantine,
            fence_finalized=bool(finalized),
        )

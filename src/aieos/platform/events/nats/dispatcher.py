"""Tenant-scoped outbox dispatch-once publisher."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Callable
from uuid import UUID

from aieos.platform.events.constants import ERROR_RETRY_EXHAUSTED
from aieos.platform.events.nats.publisher import EventPublisher
from aieos.platform.events.persistence.repositories import (
    SqlAlchemyOutboxDispatcherRepository,
)


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
        now = self._clock()
        claimed = self._repository.claim_once(
            tenant_id=tenant_id,
            claimed_by=self._config.claimed_by,
            now=now,
            claim_until=now + self._config.claim_lease,
        )
        if claimed is None:
            return False
        fence_by = claimed.claimed_by or self._config.claimed_by
        fence_attempt = claimed.attempt_count
        result = await self._publisher.publish(claimed)
        if result.published and result.ack is not None:
            return self._repository.mark_published(
                tenant_id=tenant_id,
                event_id=claimed.event_id.value,
                claimed_by=fence_by,
                attempt_count=fence_attempt,
                published_at=self._clock(),
                broker_stream=result.ack.stream,
                broker_sequence=result.ack.sequence,
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
        self._repository.release_for_retry(
            tenant_id=tenant_id,
            event_id=claimed.event_id.value,
            claimed_by=fence_by,
            attempt_count=fence_attempt,
            available_at=self._clock() + self._config.retry_delay,
            error_code=error_code,
            quarantine=quarantine,
        )
        return False

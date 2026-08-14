"""Tenant-scoped workflow start and command dispatchers (dispatch-once)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Awaitable, Callable
from uuid import UUID

from aieos.platform.workflows.constants import ERROR_RETRY_EXHAUSTED
from aieos.platform.workflows.persistence.repositories import (
    SqlAlchemyWorkflowDispatcherRepository,
)
from aieos.platform.workflows.temporal.gateway import TemporalReviewGateway


@dataclass(frozen=True, slots=True)
class DispatcherConfig:
    claim_lease: timedelta
    max_attempts: int
    retry_delay: timedelta
    claimed_by: str
    result_timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if self.claim_lease.total_seconds() <= 0:
            raise ValueError("claim_lease must be positive")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if self.retry_delay.total_seconds() < 0:
            raise ValueError("retry_delay must be non-negative")
        if not self.claimed_by.strip():
            raise ValueError("claimed_by must be non-empty")


class ContentReviewStartDispatcher:
    def __init__(
        self,
        repository: SqlAlchemyWorkflowDispatcherRepository,
        gateway: TemporalReviewGateway,
        config: DispatcherConfig,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._gateway = gateway
        self._config = config
        self._clock = clock or (lambda: datetime.now(UTC))

    async def dispatch_once(self, tenant_id: UUID) -> bool:
        now = self._clock()
        claimed = self._repository.claim_start_intent(
            tenant_id=tenant_id,
            claimed_by=self._config.claimed_by,
            now=now,
            claim_until=now + self._config.claim_lease,
        )
        if claimed is None:
            return False
        result = await self._gateway.start_content_review(
            temporal_workflow_id=claimed.temporal_workflow_id,
            task_queue=claimed.task_queue,
            start_input=dict(claimed.input),
        )
        if result.delivered:
            self._repository.mark_start_delivered(
                tenant_id=tenant_id,
                workflow_start_intent_id=claimed.workflow_start_intent_id.value,
                delivered_at=self._clock(),
            )
            return True
        quarantine = result.permanent or claimed.attempt_count >= self._config.max_attempts
        error_code = (
            ERROR_RETRY_EXHAUSTED
            if (not result.permanent and claimed.attempt_count >= self._config.max_attempts)
            else (result.error_code or ERROR_RETRY_EXHAUSTED)
        )
        self._repository.release_start_for_retry(
            tenant_id=tenant_id,
            workflow_start_intent_id=claimed.workflow_start_intent_id.value,
            available_at=self._clock() + self._config.retry_delay,
            error_code=error_code,
            quarantine=quarantine,
        )
        return False


class ContentReviewCommandDispatcher:
    def __init__(
        self,
        repository: SqlAlchemyWorkflowDispatcherRepository,
        gateway: TemporalReviewGateway,
        config: DispatcherConfig,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._gateway = gateway
        self._config = config
        self._clock = clock or (lambda: datetime.now(UTC))

    async def dispatch_once(self, tenant_id: UUID) -> bool:
        now = self._clock()
        claimed = self._repository.claim_command_intent(
            tenant_id=tenant_id,
            claimed_by=self._config.claimed_by,
            now=now,
            claim_until=now + self._config.claim_lease,
        )
        if claimed is None:
            return False
        result = await self._gateway.deliver_review_decision(
            temporal_workflow_id=claimed.temporal_workflow_id,
            command_payload=dict(claimed.payload),
            result_timeout_seconds=self._config.result_timeout_seconds,
        )
        if result.delivered:
            self._repository.mark_command_delivered(
                tenant_id=tenant_id,
                workflow_command_intent_id=claimed.workflow_command_intent_id.value,
                delivered_at=self._clock(),
            )
            return True
        quarantine = result.permanent or claimed.attempt_count >= self._config.max_attempts
        error_code = (
            ERROR_RETRY_EXHAUSTED
            if (not result.permanent and claimed.attempt_count >= self._config.max_attempts)
            else (result.error_code or ERROR_RETRY_EXHAUSTED)
        )
        self._repository.release_command_for_retry(
            tenant_id=tenant_id,
            workflow_command_intent_id=claimed.workflow_command_intent_id.value,
            available_at=self._clock() + self._config.retry_delay,
            error_code=error_code,
            quarantine=quarantine,
        )
        return False


# Awaitable kept for typing clarity in future hooks.
_ = Awaitable

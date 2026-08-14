"""Framework-neutral event and outbox models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping
from uuid import UUID

from aieos.platform.events.identities import EventId


@dataclass(frozen=True, slots=True)
class MutationEventContext:
    """Trusted mutation provenance for event envelopes. No FastAPI/NATS types."""

    correlation_id: UUID
    causation_id: UUID
    actor_principal_id: UUID
    effective_actor_id: UUID


@dataclass(frozen=True, slots=True)
class OutboxMessage:
    event_id: EventId
    tenant_id: UUID
    event_type: str
    subject: str
    aggregate_type: str
    aggregate_id: UUID
    aggregate_revision: int
    envelope: Mapping[str, object]
    status: str
    attempt_count: int
    available_at: datetime
    claimed_by: str | None
    claimed_until: datetime | None
    published_at: datetime | None
    broker_stream: str | None
    broker_sequence: int | None
    last_error_code: str | None
    created_at: datetime

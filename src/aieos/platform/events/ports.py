"""Application-facing outbox insert port. No NATS / claim / publish."""

from __future__ import annotations

from typing import Protocol

from aieos.platform.events.models import OutboxMessage


class OutboxRepository(Protocol):
    def insert(self, message: OutboxMessage) -> None: ...

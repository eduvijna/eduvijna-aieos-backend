"""Idempotency persistence port. No update/delete. No FastAPI types."""

from __future__ import annotations

from typing import Protocol

from aieos.platform.idempotency.models import IdempotencyOutcome, IdempotencyScope


class IdempotencyRepository(Protocol):
    def acquire_scope(self, scope: IdempotencyScope) -> None: ...

    def get(self, scope: IdempotencyScope) -> IdempotencyOutcome | None: ...

    def insert(self, outcome: IdempotencyOutcome) -> None: ...

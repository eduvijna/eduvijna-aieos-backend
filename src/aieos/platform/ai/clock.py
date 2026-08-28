"""UTC clock helpers for generation lease timing (TOS-DEV03R2)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

UtcNow = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(UTC)


class ControllableClock:
    """Deterministic UTC clock for lease/heartbeat tests."""

    def __init__(self, start: datetime) -> None:
        self._now = start

    def advance(self, *, seconds: float = 0) -> datetime:
        if seconds:
            self._now = self._now + timedelta(seconds=seconds)
        return self._now

    def __call__(self) -> datetime:
        return self._now

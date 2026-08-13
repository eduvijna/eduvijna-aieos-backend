"""Idempotency-Key header parser. Does not echo the raw key."""

from __future__ import annotations

from aieos.platform.api.http_errors import (
    IdempotencyKeyRequiredError,
    InvalidIdempotencyKeyError,
)

_MAX_UTF8_BYTES = 255


def parse_idempotency_key(raw: str | None) -> str:
    if raw is None or raw == "":
        raise IdempotencyKeyRequiredError()
    if not raw.strip():
        raise InvalidIdempotencyKeyError()
    if len(raw.encode("utf-8")) > _MAX_UTF8_BYTES:
        raise InvalidIdempotencyKeyError()
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in raw):
        raise InvalidIdempotencyKeyError()
    return raw

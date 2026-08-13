"""Tamper-resistant opaque keyset cursors. No production default signing key."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID


class InvalidCursorError(Exception):
    """List cursor is malformed, forged, unsupported, or bound to another tenant."""


@dataclass(frozen=True, slots=True)
class ListCursor:
    tenant_id: UUID
    created_at: datetime
    content_id: UUID


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(token: str) -> bytes:
    padding = "=" * (-len(token) % 4)
    return base64.urlsafe_b64decode(token + padding)


def _canon_dt(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class CursorCodec:
    version = 1

    def __init__(self, signing_key: bytes) -> None:
        if not signing_key:
            raise ValueError(
                "cursor signing key must be injected; there is no production default"
            )
        self._key = signing_key

    def encode(self, cursor: ListCursor) -> str:
        payload = json.dumps(
            {
                "v": self.version,
                "tenant_id": str(cursor.tenant_id),
                "created_at": _canon_dt(cursor.created_at),
                "content_id": str(cursor.content_id),
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        signature = hmac.new(self._key, payload, hashlib.sha256).digest()
        return f"{_b64url_encode(payload)}.{_b64url_encode(signature)}"

    def decode(self, token: str, *, expected_tenant_id: UUID) -> ListCursor:
        try:
            blob, sig_b64 = token.split(".", 1)
            payload = _b64url_decode(blob)
            signature = _b64url_decode(sig_b64)
        except (ValueError, Exception) as exc:
            raise InvalidCursorError("invalid cursor") from exc
        expected = hmac.new(self._key, payload, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise InvalidCursorError("invalid cursor")
        try:
            data = json.loads(payload.decode("utf-8"))
            if data.get("v") != self.version:
                raise InvalidCursorError("invalid cursor")
            tenant_id = UUID(str(data["tenant_id"]))
            content_id = UUID(str(data["content_id"]))
            created_at = datetime.fromisoformat(
                str(data["created_at"]).replace("Z", "+00:00")
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise InvalidCursorError("invalid cursor") from exc
        if tenant_id != expected_tenant_id:
            raise InvalidCursorError("invalid cursor")
        return ListCursor(
            tenant_id=tenant_id,
            created_at=created_at,
            content_id=content_id,
        )

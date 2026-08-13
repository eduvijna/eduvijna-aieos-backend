"""Canonical SHA-256 helpers for idempotency keys and request fingerprints."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from uuid import UUID


def sha256_hex(value: str | bytes) -> str:
    data = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(data).hexdigest()


def hash_idempotency_key(raw_key: str) -> str:
    return sha256_hex(raw_key)


def fingerprint_material(material: Mapping[str, object]) -> str:
    canonical = json.dumps(
        material,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=_json_default,
    )
    return sha256_hex(canonical)


def _json_default(value: object) -> object:
    if isinstance(value, UUID):
        return str(value)
    raise TypeError(f"unfingerprintable type {type(value).__name__}")


def advisory_lock_key(scope: object) -> int:
    tenant_id = getattr(scope, "tenant_id")
    principal_id = getattr(scope, "principal_id")
    operation = getattr(scope, "operation")
    key_sha256 = getattr(scope, "key_sha256")
    material = f"{tenant_id}|{principal_id}|{operation}|{key_sha256}"
    return int.from_bytes(hashlib.sha256(material.encode("utf-8")).digest()[:8], "big", signed=True)

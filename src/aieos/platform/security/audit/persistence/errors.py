"""Sanitized infrastructure errors for security audit persistence."""

from __future__ import annotations

from typing import NoReturn


class SecurityAuditPersistenceError(Exception):
    """Sanitized audit persistence failure. Cause preserves the original."""

    def __init__(self, message: str = "security audit persistence operation failed") -> None:
        super().__init__(message)


def reraise_as_audit_persistence_error(exc: BaseException) -> NoReturn:
    if isinstance(exc, SecurityAuditPersistenceError):
        raise exc
    raise SecurityAuditPersistenceError(
        "security audit persistence operation failed"
    ) from exc

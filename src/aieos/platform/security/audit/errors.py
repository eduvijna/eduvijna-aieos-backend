"""Security mutation-audit errors. Framework-neutral."""

from __future__ import annotations


class InvalidSecurityAuditError(ValueError):
    """Raised when a security mutation-audit contract cannot be constructed."""

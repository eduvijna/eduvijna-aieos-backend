"""Governed runtime-configuration errors. Never embed secret values."""

from __future__ import annotations


class RuntimeConfigurationError(Exception):
    """Fail-closed configuration validation failure.

    Messages may name environment variable keys and high-level reasons.
    They must never contain passwords, raw DSNs, or signing-key material.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)

    def __str__(self) -> str:
        return super().__str__()

    def __repr__(self) -> str:
        return f"RuntimeConfigurationError({super().__str__()!r})"

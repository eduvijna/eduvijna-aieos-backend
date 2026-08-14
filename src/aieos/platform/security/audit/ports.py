"""Framework-neutral security mutation-audit persistence port."""

from __future__ import annotations

from typing import Protocol

from aieos.platform.security.audit.models import SecurityMutationAuditRecord


class SecurityMutationAuditRepository(Protocol):
    """Insert-only audit participation in a caller-owned UoW.

    No independent commit/rollback. No update/delete. No broad read/search API.
    """

    def insert(self, record: SecurityMutationAuditRecord) -> None: ...

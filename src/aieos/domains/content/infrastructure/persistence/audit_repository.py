"""Content-boundary adapter over the platform security audit repository."""

from __future__ import annotations

from sqlalchemy.engine import Connection

from aieos.domains.content.application.errors import PersistenceOperationFailed
from aieos.platform.security.audit.models import SecurityMutationAuditRecord
from aieos.platform.security.audit.persistence.errors import (
    SecurityAuditPersistenceError,
)
from aieos.platform.security.audit.persistence.repositories import (
    SqlAlchemySecurityMutationAuditRepository,
)


class ContentSecurityMutationAuditRepository:
    """Insert-only audit adapter. Translates platform errors to Content errors."""

    def __init__(self, connection: Connection) -> None:
        self._delegate = SqlAlchemySecurityMutationAuditRepository(connection)

    def insert(self, record: SecurityMutationAuditRecord) -> None:
        try:
            self._delegate.insert(record)
        except SecurityAuditPersistenceError as exc:
            raise PersistenceOperationFailed(
                "content persistence operation failed"
            ) from exc

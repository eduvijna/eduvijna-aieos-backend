"""Asset-boundary adapter over the platform security audit repository."""

from __future__ import annotations

from sqlalchemy.engine import Connection

from aieos.domains.asset.application.mutation_errors import AssetPersistenceFailed
from aieos.platform.security.audit.models import SecurityMutationAuditRecord
from aieos.platform.security.audit.persistence.errors import (
    SecurityAuditPersistenceError,
)
from aieos.platform.security.audit.persistence.repositories import (
    SqlAlchemySecurityMutationAuditRepository,
)


class AssetSecurityMutationAuditRepository:
    """Insert-only audit adapter. Translates platform errors to Asset errors."""

    def __init__(self, connection: Connection) -> None:
        self._delegate = SqlAlchemySecurityMutationAuditRepository(connection)

    def insert(self, record: SecurityMutationAuditRecord) -> None:
        try:
            self._delegate.insert(record)
        except SecurityAuditPersistenceError as exc:
            raise AssetPersistenceFailed(
                "asset persistence operation failed"
            ) from exc

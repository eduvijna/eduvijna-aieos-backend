"""Generic Content application layer (GCI-I03).

Persistence ports and append orchestration. No SQLAlchemy, HTTP, NATS,
Temporal, or AI-provider imports.
"""

from aieos.domains.content.application.errors import (
    AggregateRevisionConflict,
    ContentApplicationError,
    ContentNotFound,
    PersistenceInvariantViolation,
    TenantContextMismatch,
    VersionAlreadyExists,
    VersionLineageConflict,
)
from aieos.domains.content.application.models import (
    AppendContentVersionCommand,
    AppendContentVersionResult,
    LockedContentHead,
)
from aieos.domains.content.application.ports import (
    ContentRepository,
    ContentUnitOfWork,
    ContentUnitOfWorkFactory,
    ContentVersionRepository,
)
from aieos.domains.content.application.services import AppendContentVersionService

__all__ = [
    "AggregateRevisionConflict",
    "AppendContentVersionCommand",
    "AppendContentVersionResult",
    "AppendContentVersionService",
    "ContentApplicationError",
    "ContentNotFound",
    "ContentRepository",
    "ContentUnitOfWork",
    "ContentUnitOfWorkFactory",
    "ContentVersionRepository",
    "LockedContentHead",
    "PersistenceInvariantViolation",
    "TenantContextMismatch",
    "VersionAlreadyExists",
    "VersionLineageConflict",
]

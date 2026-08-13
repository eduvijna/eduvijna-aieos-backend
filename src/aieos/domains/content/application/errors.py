"""Technology-neutral Generic Content application errors.

No HTTP status codes, Problem Details, SQLAlchemy, or driver exceptions.
"""

from __future__ import annotations


class ContentApplicationError(Exception):
    """Base error for Generic Content application/persistence-boundary failures."""


class ContentNotFound(ContentApplicationError):
    """Target Content is not visible in the current execution tenant."""


class AggregateRevisionConflict(ContentApplicationError):
    """Expected aggregate revision does not match the locked/stored head."""


class VersionLineageConflict(ContentApplicationError):
    """Append would violate linear ContentVersion history."""


class TenantContextMismatch(ContentApplicationError):
    """Execution tenant does not match the ContentVersion tenant."""


class VersionAlreadyExists(ContentApplicationError):
    """A ContentVersion with the same identity or version number already exists."""


class PersistenceInvariantViolation(ContentApplicationError):
    """A persistence invariant failed (payload, provenance, or database check)."""


class PersistenceOperationFailed(ContentApplicationError):
    """Infrastructure/transaction/connection/driver failure, not a business conflict."""

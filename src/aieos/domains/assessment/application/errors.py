"""Technology-neutral Assessment application errors.

No HTTP status codes, Problem Details, SQLAlchemy, or driver exceptions.
"""

from __future__ import annotations


class AssessmentApplicationError(Exception):
    """Base error for Assessment application/persistence-boundary failures."""


class PersistenceOperationFailed(AssessmentApplicationError):
    """Infrastructure/transaction/connection/driver failure, not a business conflict."""


class PersistenceInvariantViolation(AssessmentApplicationError):
    """An Assessment persistence invariant failed (database check or visibility)."""

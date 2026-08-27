"""Technology-neutral Teaching application errors.

No HTTP status codes, Problem Details, SQLAlchemy, or driver exceptions.
"""

from __future__ import annotations


class TeachingApplicationError(Exception):
    """Base error for Teaching application/persistence-boundary failures."""


class TeachingWorkNotFound(TeachingApplicationError):
    """Target TeachingWork is not visible in the current execution tenant."""


class TeachingWorkForbidden(TeachingApplicationError):
    """The principal does not own the target TeachingWork."""


class AggregateRevisionConflict(TeachingApplicationError):
    """Expected aggregate revision does not match the locked/stored head."""


class InvalidTeachingWorkRequest(TeachingApplicationError):
    """A create/refine/query request failed application validation."""


class IdempotencyKeyReused(TeachingApplicationError):
    """The idempotency key was already bound to a different request fingerprint."""


class PersistenceOperationFailed(TeachingApplicationError):
    """Infrastructure/transaction/connection/driver failure, not a business conflict."""


class PersistenceInvariantViolation(TeachingApplicationError):
    """A Teaching persistence invariant failed (database check or visibility)."""


class TeacherOsMissionUnavailable(TeachingApplicationError):
    """A required Today's Mission projection input could not be composed."""

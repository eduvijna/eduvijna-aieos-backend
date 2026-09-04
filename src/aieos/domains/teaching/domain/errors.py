"""Teaching domain errors. No HTTP, SQL, or driver concepts."""

from __future__ import annotations


class TeachingDomainError(Exception):
    """Base error for Teaching domain invariant violations."""


class InvalidTeachingIdentityError(TeachingDomainError):
    """A Teaching-owned identity value failed its identity contract."""


class InvalidAggregateRevisionError(TeachingDomainError):
    """aggregate_revision is not a non-negative integer."""


class InvalidIntentTypeError(TeachingDomainError):
    """The requested intent type is not a registered Teaching intent type."""


class InvalidTeachingWorkError(TeachingDomainError):
    """A TeachingWork aggregate field failed validation."""


class InvalidRemediationOriginError(TeachingDomainError):
    """A TeachingWorkRemediationOrigin field or construction invariant failed."""


class InvalidTeachingAssignmentError(TeachingDomainError):
    """A TeachingAssignment aggregate field or lifecycle transition failed."""


class InvalidTeachingExecutionError(TeachingDomainError):
    """A TeachingExecution aggregate field or lifecycle transition failed."""


class InvalidTeachingExecutionObservationError(TeachingDomainError):
    """A TeachingExecutionObservation field or mutation failed."""

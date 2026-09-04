"""Assessment domain errors. No HTTP, SQL, or driver concepts."""

from __future__ import annotations


class AssessmentDomainError(Exception):
    """Base error for Assessment domain invariant violations."""


class InvalidAssessmentIdentityError(AssessmentDomainError):
    """An Assessment-owned identity value failed its identity contract."""


class InvalidAggregateRevisionError(AssessmentDomainError):
    """aggregate_revision is not a non-negative integer."""


class InvalidClassroomAssessmentError(AssessmentDomainError):
    """A ClassroomAssessment aggregate field or lifecycle transition failed."""

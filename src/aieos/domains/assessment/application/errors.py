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


class IdempotencyKeyReused(AssessmentApplicationError):
    """Same Idempotency-Key bound to different canonical material."""


class AggregateRevisionConflict(AssessmentApplicationError):
    """If-Match / CAS lost race against ClassroomAssessment aggregate_revision."""


class InvalidClassroomAssessmentRequest(AssessmentApplicationError):
    """Intrinsic or command validation failed for an Assessment mutation."""


class ClassroomAssessmentNotFound(AssessmentApplicationError):
    """Assessment is not visible in the execution tenant for the caller."""


class ClassroomAssessmentForbidden(AssessmentApplicationError):
    """Assessment exists but is owned by a different represented teacher."""


class ClassroomAssessmentNotRecorded(AssessmentApplicationError):
    """Mutation requires RECORDED lifecycle; VOIDED is terminal."""


class SchoolContextUnavailable(AssessmentApplicationError):
    """School Context current-class authority is not composed or unreachable."""


class ClassRefNotAssignable(AssessmentApplicationError):
    """ClassRef is not a current teaching target for the represented teacher.

    Bootstrap semantics reuse School Context assignable-class proof as current
    class authority. This is not a permanent assignable == assessable claim.
    """


class ContentNotFoundForAssessment(AssessmentApplicationError):
    """Content is not visible in the execution tenant."""


class ContentNotEligibleForAssessment(AssessmentApplicationError):
    """Content type is not Assessment-eligible (quiz/worksheet/homework only)."""


class ContentVersionMismatch(AssessmentApplicationError):
    """Requested ContentVersion fails Case C current publication or exact binding."""


class TeachingExecutionNotFound(AssessmentApplicationError):
    """TeachingExecution is not visible for Case A composition."""


class TeachingExecutionForbidden(AssessmentApplicationError):
    """TeachingExecution is owned by a different teacher."""


class TeachingExecutionNotCompleted(AssessmentApplicationError):
    """Case A requires COMPLETED TeachingExecution."""


class TeachingExecutionBindingMismatch(AssessmentApplicationError):
    """Exact ContentVersion binding or class/work composition does not match execution."""


class TeachingAssignmentNotFound(AssessmentApplicationError):
    """TeachingAssignment is not visible for Case B composition."""


class TeachingAssignmentForbidden(AssessmentApplicationError):
    """TeachingAssignment is owned by a different teacher."""


class TeachingAssignmentCompositionMismatch(AssessmentApplicationError):
    """Assignment class/content/audience/work composition does not match request."""


class TeachingWorkNotFound(AssessmentApplicationError):
    """Optional TeachingWork is not visible in the execution tenant."""


class TeachingWorkForbidden(AssessmentApplicationError):
    """Optional TeachingWork is owned by a different teacher."""


class CompositionConflict(AssessmentApplicationError):
    """Execution and assignment composition facts do not mutually agree."""

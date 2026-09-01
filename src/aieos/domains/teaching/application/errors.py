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


class WorkGenerationPreconditionRequired(TeachingApplicationError):
    """If-Match is required for generation."""


class WorkGenerationRevisionConflict(TeachingApplicationError):
    """If-Match does not match the current Work revision for generation."""


class WorkGenerationInProgress(TeachingApplicationError):
    """A generation with this idempotency key is already RUNNING."""


class WorkGenerationAlreadyExists(TeachingApplicationError):
    """This Work already has a successful generation artifact."""

    def __init__(self, message: str = "work generation already exists") -> None:
        super().__init__(message)
        self.existing_generation_run_id: object | None = None
        self.existing_content_id: object | None = None
        self.existing_version_id: object | None = None


class GenerationIdempotencyConflict(TeachingApplicationError):
    """Idempotency-Key reused with a different request fingerprint."""


class ModelProviderUnavailableError(TeachingApplicationError):
    """Model provider is unavailable for generation."""


class ModelGenerationFailedError(TeachingApplicationError):
    """Model generation failed."""


class ModelOutputInvalidError(TeachingApplicationError):
    """Model output could not be parsed into the required schema."""


class EducationalQualityFailedError(TeachingApplicationError):
    """Educational Quality Baseline rejected the generated draft."""

    def __init__(
        self,
        message: str = "educational quality baseline failed",
        *,
        educational_quality: object | None = None,
    ) -> None:
        super().__init__(message)
        self.educational_quality = educational_quality


class GenerationServiceUnavailable(TeachingApplicationError):
    """Generation composition is not available in this runtime."""


class SchoolContextUnavailable(TeachingApplicationError):
    """School Context provider is unavailable or not composed in this runtime."""


class SchoolContextContractError(TeachingApplicationError):
    """School Context provider returned a structurally invalid response."""


class TeachingAssignmentNotFound(TeachingApplicationError):
    """Target TeachingAssignment is not visible in the execution tenant."""


class TeachingAssignmentForbidden(TeachingApplicationError):
    """The principal does not own the target TeachingAssignment."""


class ClassRefNotAssignable(TeachingApplicationError):
    """Requested ClassRef is not currently assignable for the teacher."""


class ContentNotEligibleForAssignment(TeachingApplicationError):
    """Content is not eligible for classroom assignment."""


class ContentNotFoundForAssignment(TeachingApplicationError):
    """Requested Content is not visible for assignment."""


class ContentVersionMismatch(TeachingApplicationError):
    """Requested ContentVersion is not the published exact version."""


class TeachingAssignmentNotActive(TeachingApplicationError):
    """Mutation requires an ACTIVE TeachingAssignment."""


class SourceWorkNotFound(TeachingApplicationError):
    """Optional source TeachingWork is not visible."""


class SourceWorkForbidden(TeachingApplicationError):
    """Optional source TeachingWork is owned by a different teacher."""


class InvalidTeachingAssignmentRequest(TeachingApplicationError):
    """An assignment command failed application validation."""


class ContentMaterializationFailedError(TeachingApplicationError):
    """Content materialization failed after model generation succeeded."""


class PreparationRecoveryInvariantError(TeachingApplicationError):
    """Partial or corrupt preparation Content bindings violate the exact-six invariant."""

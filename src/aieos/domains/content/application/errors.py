"""Technology-neutral Generic Content application errors.

No HTTP status codes, Problem Details, SQLAlchemy, or driver exceptions.
"""

from __future__ import annotations


class ContentApplicationError(Exception):
    """Base error for Generic Content application/persistence-boundary failures."""


class ContentNotFound(ContentApplicationError):
    """Target Content is not visible in the current execution tenant."""


class ContentVersionNotFound(ContentApplicationError):
    """Target ContentVersion is not visible for the path Content."""


class AggregateRevisionConflict(ContentApplicationError):
    """Expected aggregate revision does not match the locked/stored head."""


class VersionLineageConflict(ContentApplicationError):
    """Append would violate linear ContentVersion history."""


class TenantContextMismatch(ContentApplicationError):
    """Execution tenant does not match the ContentVersion tenant."""


class VersionAlreadyExists(ContentApplicationError):
    """A ContentVersion with the same identity or version number already exists."""


class ContentAlreadyExists(ContentApplicationError):
    """A Content aggregate with the same identity already exists."""


class PersistenceInvariantViolation(ContentApplicationError):
    """A persistence invariant failed (payload, provenance, or database check)."""


class PersistenceOperationFailed(ContentApplicationError):
    """Infrastructure/transaction/connection/driver failure, not a business conflict."""


class UnknownContentType(ContentApplicationError):
    """Requested content_type is not in the registered catalog."""


class InvalidContentRequest(ContentApplicationError):
    """Create or query request failed application validation."""


class ContentVersionAppendNotAllowed(ContentApplicationError):
    """External append is not allowed in the current stewardship state."""


class ContentSchemaNotFound(ContentApplicationError):
    """Requested schema_id/schema_version is not registered."""


class ContentSchemaMismatch(ContentApplicationError):
    """Registered schema content_type does not match the Content."""


class ContentPayloadInvalid(ContentApplicationError):
    """Payload failed the registered schema validator or canonical payload rules."""


class IdempotencyKeyReused(ContentApplicationError):
    """The idempotency key was already bound to a different request fingerprint."""


class ReviewForbidden(ContentApplicationError):
    """Current principal lacks the required review capability."""


class ReviewCommentRejected(ContentApplicationError):
    """The review comment was rejected by the comment governance policy."""


class ReviewSubmitNotAllowed(ContentApplicationError):
    """Submit-for-review is not allowed in the current stewardship state."""


class ReviewDecisionNotAllowed(ContentApplicationError):
    """A review decision is not allowed in the current stewardship state."""


class ReviewVersionNotCurrent(ContentApplicationError):
    """The requested version is not the Content current_version_id."""


class ReviewRequiresNewVersion(ContentApplicationError):
    """That immutable version already has a terminal ReviewDecision."""


class ReviewAlreadyDecided(ContentApplicationError):
    """A terminal ReviewDecision already exists for the exact version."""

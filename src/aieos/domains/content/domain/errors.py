"""Generic Content domain errors.

Semantic domain failures only. No HTTP status codes, problem-details
payloads, SQLAlchemy, Temporal, or NATS exceptions.
"""

from __future__ import annotations


class ContentDomainError(Exception):
    """Base error for Generic Content domain invariant failures."""


class InvalidContentIdentityError(ContentDomainError):
    """Raised when a Content-domain identity is missing, malformed, or not UUIDv7."""


class InvalidStewardshipStateError(ContentDomainError):
    """Raised when a stewardship state is not in the frozen vocabulary."""


class InvalidOriginError(ContentDomainError):
    """Raised when a Content origin is not in the frozen vocabulary."""


class InvalidVersionNumberError(ContentDomainError):
    """Raised when a business version_number is not a positive integer."""


class InvalidAggregateRevisionError(ContentDomainError):
    """Raised when aggregate_revision is not a non-negative integer."""


class InvalidContentTypeError(ContentDomainError):
    """Raised when a content type identifier is empty or invalid."""


class InvalidPayloadError(ContentDomainError):
    """Raised when a ContentVersion payload cannot be represented canonically."""


class ContentVersionImmutabilityError(ContentDomainError):
    """Raised when a committed ContentVersion would be mutated in place."""


class ParentLineageError(ContentDomainError):
    """Raised when parent lineage is not linear within the same tenant + Content."""


class SchemaNotFoundError(ContentDomainError):
    """Raised when a schema_id/schema_version cannot be resolved."""


class DuplicateSchemaVersionError(ContentDomainError):
    """Raised when registering a schema version that already exists."""


class ReviewDecisionBindingError(ContentDomainError):
    """Raised when a review decision is not bound to an exact ContentVersion."""


class InvalidReviewDecisionError(ContentDomainError):
    """Raised when a review decision is not in the frozen vocabulary."""


class PublicationBindingError(ContentDomainError):
    """Raised when a Publication is not bound to an exact ContentVersion."""


class InvalidVersionAssetRefError(ContentDomainError):
    """Raised when a VersionAssetRef association violates domain invariants."""


class InvalidAIGenerationProvenanceError(ContentDomainError):
    """Raised when AI generation provenance fails the allow-listed V1 contract."""


class InvalidMigrationSourceIdentityError(ContentDomainError):
    """Raised when migration source identity or mapping identifiers are invalid."""


class InvalidMigrationImportProvenanceError(ContentDomainError):
    """Raised when migration import provenance fails the allow-listed V1 contract."""


class InvalidContentAggregateError(ContentDomainError):
    """Raised when a Content aggregate violates domain invariants."""

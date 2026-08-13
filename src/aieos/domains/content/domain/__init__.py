"""Generic Content pure-domain contracts (GCI-I01)."""

from aieos.domains.content.domain.content import Content, ContentType
from aieos.domains.content.domain.errors import (
    ContentDomainError,
    ContentVersionImmutabilityError,
    DuplicateSchemaVersionError,
    InvalidAggregateRevisionError,
    InvalidContentAggregateError,
    InvalidContentIdentityError,
    InvalidContentTypeError,
    InvalidOriginError,
    InvalidPayloadError,
    InvalidReviewDecisionError,
    InvalidStewardshipStateError,
    InvalidVersionNumberError,
    ParentLineageError,
    PublicationBindingError,
    ReviewDecisionBindingError,
    SchemaNotFoundError,
)
from aieos.domains.content.domain.identities import (
    AggregateRevision,
    ContentId,
    ContentVersionId,
    PublicationId,
    ReviewDecisionId,
    VersionNumber,
)
from aieos.domains.content.domain.origin import (
    FROZEN_CONTENT_ORIGINS,
    ContentOrigin,
    parse_content_origin,
)
from aieos.domains.content.domain.publication import Publication
from aieos.domains.content.domain.review import (
    FROZEN_REVIEW_DECISION_CODES,
    ReviewDecision,
    ReviewDecisionCode,
    parse_review_decision_code,
)
from aieos.domains.content.domain.schema import (
    ContentSchema,
    ContentSchemaRegistry,
    RegisteredSchema,
    SchemaId,
    SchemaRef,
    SchemaVersion,
)
from aieos.domains.content.domain.states import (
    FROZEN_STEWARDSHIP_STATES,
    StewardshipState,
    parse_stewardship_state,
)
from aieos.domains.content.domain.version import (
    ContentPayload,
    ContentVersion,
    PayloadSha256,
    validate_linear_parent,
)

__all__ = [
    "AggregateRevision",
    "Content",
    "ContentDomainError",
    "ContentId",
    "ContentOrigin",
    "ContentPayload",
    "ContentSchema",
    "ContentSchemaRegistry",
    "ContentType",
    "ContentVersion",
    "ContentVersionId",
    "ContentVersionImmutabilityError",
    "DuplicateSchemaVersionError",
    "FROZEN_CONTENT_ORIGINS",
    "FROZEN_REVIEW_DECISION_CODES",
    "FROZEN_STEWARDSHIP_STATES",
    "InvalidAggregateRevisionError",
    "InvalidContentAggregateError",
    "InvalidContentIdentityError",
    "InvalidContentTypeError",
    "InvalidOriginError",
    "InvalidPayloadError",
    "InvalidReviewDecisionError",
    "InvalidStewardshipStateError",
    "InvalidVersionNumberError",
    "ParentLineageError",
    "PayloadSha256",
    "Publication",
    "PublicationBindingError",
    "PublicationId",
    "RegisteredSchema",
    "ReviewDecision",
    "ReviewDecisionBindingError",
    "ReviewDecisionCode",
    "ReviewDecisionId",
    "SchemaId",
    "SchemaNotFoundError",
    "SchemaRef",
    "SchemaVersion",
    "StewardshipState",
    "VersionNumber",
    "parse_content_origin",
    "parse_review_decision_code",
    "parse_stewardship_state",
    "validate_linear_parent",
]

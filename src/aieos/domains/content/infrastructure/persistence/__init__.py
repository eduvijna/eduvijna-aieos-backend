"""SQLAlchemy persistence mappings, repositories, and Unit of Work."""

from aieos.domains.content.infrastructure.persistence.metadata import (
    CONTENT_SCHEMA,
    content_metadata,
)
from aieos.domains.content.infrastructure.persistence.models import (
    content_versions_table,
    contents_table,
    review_decisions_table,
)
from aieos.domains.content.infrastructure.persistence.repositories import (
    SqlAlchemyContentRepository,
    SqlAlchemyContentVersionRepository,
    SqlAlchemyReviewDecisionRepository,
)
from aieos.domains.content.infrastructure.persistence.uow import (
    SqlAlchemyContentUnitOfWork,
    SqlAlchemyContentUnitOfWorkFactory,
)

__all__ = [
    "CONTENT_SCHEMA",
    "SqlAlchemyContentRepository",
    "SqlAlchemyContentUnitOfWork",
    "SqlAlchemyContentUnitOfWorkFactory",
    "SqlAlchemyContentVersionRepository",
    "SqlAlchemyReviewDecisionRepository",
    "content_metadata",
    "content_versions_table",
    "contents_table",
    "review_decisions_table",
]

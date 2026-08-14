"""SQLAlchemy persistence mappings and repositories."""

from aieos.domains.content.infrastructure.persistence.metadata import (
    CONTENT_SCHEMA,
    content_metadata,
)
from aieos.domains.content.infrastructure.persistence.models import (
    content_versions_table,
    contents_table,
    publications_table,
    review_decisions_table,
    version_asset_refs_table,
)
from aieos.domains.content.infrastructure.persistence.repositories import (
    SqlAlchemyContentRepository,
    SqlAlchemyContentVersionRepository,
    SqlAlchemyPublicationRepository,
    SqlAlchemyReviewDecisionRepository,
    SqlAlchemyVersionAssetRefRepository,
)

__all__ = [
    "CONTENT_SCHEMA",
    "SqlAlchemyContentRepository",
    "SqlAlchemyContentVersionRepository",
    "SqlAlchemyPublicationRepository",
    "SqlAlchemyReviewDecisionRepository",
    "SqlAlchemyVersionAssetRefRepository",
    "content_metadata",
    "content_versions_table",
    "contents_table",
    "publications_table",
    "review_decisions_table",
    "version_asset_refs_table",
]

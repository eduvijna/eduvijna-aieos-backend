"""SQLAlchemy persistence mappings for Generic Content. Not the domain authority."""

from aieos.domains.content.infrastructure.persistence.metadata import (
    CONTENT_SCHEMA,
    content_metadata,
)
from aieos.domains.content.infrastructure.persistence.models import (
    content_versions_table,
    contents_table,
)

__all__ = [
    "CONTENT_SCHEMA",
    "content_metadata",
    "content_versions_table",
    "contents_table",
]

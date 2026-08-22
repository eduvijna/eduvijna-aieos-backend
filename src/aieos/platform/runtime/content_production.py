"""Production Content catalog and schema registry wiring for API composition.

The production catalog and schema registry are intentionally empty. No educational
production Content type has yet been registered under separately governed
architecture authority.

This is fail-closed runtime wiring only. Future production Content mutation
activation requires separately governed production Content-type/schema registration.

Test fixtures and event contract samples are never production registry authority.
Not a database catalog.
"""

from __future__ import annotations

from aieos.domains.content.application.catalog import StaticContentTypeCatalog
from aieos.domains.content.domain.schema import ContentSchemaRegistry


def build_production_content_type_catalog() -> StaticContentTypeCatalog:
    """Return an empty production Content-type catalog."""
    return StaticContentTypeCatalog(())


def build_production_content_schema_registry() -> ContentSchemaRegistry:
    """Return an empty production schema registry."""
    return ContentSchemaRegistry()

"""Production Content catalog and schema registry wiring for API composition.

Uses the governed contract baseline content type ``test.generic`` (see
``contracts/events/content/content.created.v1.json``). Not a database catalog.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from aieos.domains.content.application.catalog import StaticContentTypeCatalog
from aieos.domains.content.domain.errors import InvalidPayloadError
from aieos.domains.content.domain.schema import (
    ContentSchemaRegistry,
    SchemaId,
    SchemaVersion,
)

PRODUCTION_CONTENT_TYPES: frozenset[str] = frozenset({"test.generic"})


@dataclass(frozen=True, slots=True)
class _GenericContentSchemaV1:
    content_type: str
    schema_id: SchemaId
    schema_version: SchemaVersion
    required_keys: tuple[str, ...]

    def validate(self, payload: Mapping[str, object]) -> None:
        missing = [key for key in self.required_keys if key not in payload]
        if missing:
            raise InvalidPayloadError("payload failed schema validation")


def build_production_content_type_catalog() -> StaticContentTypeCatalog:
    return StaticContentTypeCatalog(PRODUCTION_CONTENT_TYPES)


def build_production_content_schema_registry() -> ContentSchemaRegistry:
    registry = ContentSchemaRegistry()
    registry.register(
        _GenericContentSchemaV1(
            content_type="test.generic",
            schema_id=SchemaId("test.generic"),
            schema_version=SchemaVersion(1),
            required_keys=("marker",),
        )
    )
    registry.register(
        _GenericContentSchemaV1(
            content_type="test.generic",
            schema_id=SchemaId("test.generic"),
            schema_version=SchemaVersion(2),
            required_keys=("marker", "extra"),
        )
    )
    return registry

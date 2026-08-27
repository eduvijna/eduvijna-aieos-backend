"""Development-boundary Content schema fixtures.

Uses the same synthetic ``test.generic`` type already employed by the
governed test harness. Production catalog/registry remain empty and fail-closed.
No new educational Content type architecture is introduced in TOS-DEV01.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from aieos.domains.content.domain.errors import InvalidPayloadError
from aieos.domains.content.domain.schema import (
    ContentSchemaRegistry,
    SchemaId,
    SchemaVersion,
)

DEV_CONTENT_TYPE = "test.generic"
DEV_SCHEMA_ID = "test.generic"
DEV_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class DevelopmentFixtureSchema:
    content_type: str
    schema_id: SchemaId
    schema_version: SchemaVersion
    required_keys: tuple[str, ...] = ()

    def validate(self, payload: Mapping[str, object]) -> None:
        missing = [key for key in self.required_keys if key not in payload]
        if missing:
            raise InvalidPayloadError(
                f"development fixture schema missing keys: {missing}"
            )


DEV_GENERIC_V1 = DevelopmentFixtureSchema(
    content_type=DEV_CONTENT_TYPE,
    schema_id=SchemaId(DEV_SCHEMA_ID),
    schema_version=SchemaVersion(DEV_SCHEMA_VERSION),
    required_keys=("marker",),
)


def build_development_schema_registry() -> ContentSchemaRegistry:
    registry = ContentSchemaRegistry()
    registry.register(DEV_GENERIC_V1)
    return registry

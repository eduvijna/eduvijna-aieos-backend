"""Development-boundary Content schema fixtures.

Registers synthetic ``test.generic`` and real ``worksheet`` for NON_PRODUCTION
Teacher OS development composition. Production catalog/registry stay empty.
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
from aieos.domains.education.schema import WORKSHEET_V1_SCHEMA

DEV_CONTENT_TYPE = "test.generic"
DEV_SCHEMA_ID = "test.generic"
DEV_SCHEMA_VERSION = 1
DEV_WORKSHEET_CONTENT_TYPE = "worksheet"


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
    registry.register(WORKSHEET_V1_SCHEMA)
    return registry

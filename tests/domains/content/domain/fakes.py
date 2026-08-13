"""Clearly named test-only schema fixtures. Not real educational Content types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from aieos.domains.content.domain.schema import SchemaId, SchemaVersion


@dataclass(frozen=True, slots=True)
class TestFixtureSchema:
    content_type: str
    schema_id: SchemaId
    schema_version: SchemaVersion
    required_keys: tuple[str, ...] = ()

    def validate(self, payload: Mapping[str, object]) -> None:
        missing = [key for key in self.required_keys if key not in payload]
        if missing:
            raise ValueError(f"test fixture schema missing keys: {missing}")


TEST_GENERIC_V1 = TestFixtureSchema(
    content_type="test.generic",
    schema_id=SchemaId("test.generic"),
    schema_version=SchemaVersion(1),
    required_keys=("marker",),
)

TEST_GENERIC_V2 = TestFixtureSchema(
    content_type="test.generic",
    schema_id=SchemaId("test.generic"),
    schema_version=SchemaVersion(2),
    required_keys=("marker", "extra"),
)

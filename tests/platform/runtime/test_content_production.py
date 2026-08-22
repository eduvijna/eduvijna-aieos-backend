"""Fail-closed production Content catalog/registry wiring tests."""

from __future__ import annotations

import pytest

from aieos.domains.content.domain.errors import SchemaNotFoundError
from aieos.domains.content.domain.schema import SchemaId, SchemaVersion
from aieos.platform.runtime.content_production import (
    build_production_content_schema_registry,
    build_production_content_type_catalog,
)

pytestmark = pytest.mark.ped_i01


def test_production_catalog_is_empty() -> None:
    catalog = build_production_content_type_catalog()
    assert not catalog.contains("test.generic")


def test_production_catalog_rejects_test_prefix_types() -> None:
    catalog = build_production_content_type_catalog()
    for content_type in ("test.generic", "test.other", "test.lesson"):
        assert not catalog.contains(content_type)


def test_production_schema_registry_is_empty() -> None:
    registry = build_production_content_schema_registry()
    with pytest.raises(SchemaNotFoundError):
        registry.get(SchemaId("test.generic"), SchemaVersion(1))
    with pytest.raises(SchemaNotFoundError):
        registry.get(SchemaId("test.generic"), SchemaVersion(2))

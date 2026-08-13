"""GCI-I05 OpenAPI operationIds and required mutation headers."""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest

from aieos.domains.content.application.catalog import StaticContentTypeCatalog
from aieos.domains.content.domain.schema import ContentSchemaRegistry
from aieos.platform.api.app import create_app
from aieos.platform.api.openapi import build_openapi, canonical_openapi_json
from tests.fakes import StubSecurityContextResolver

pytestmark = pytest.mark.gci_i05


class _UnusedUowFactory:
    def __call__(self, execution_tenant_id):
        raise AssertionError("OpenAPI export must not touch persistence")


def _schema() -> dict:
    app = create_app(
        uow_factory=_UnusedUowFactory(),
        security_resolver=StubSecurityContextResolver(uuid4(), uuid4()),
        content_types=StaticContentTypeCatalog({"test.generic"}),
        cursor_signing_key=b"gci-i05-openapi-export-key",
        schema_registry=ContentSchemaRegistry(),
        idempotency_retention=timedelta(hours=24),
    )
    return build_openapi(app)


def test_version_operations_and_required_headers() -> None:
    schema = _schema()
    assert schema["openapi"].startswith("3.1")
    paths = schema["paths"]
    assert "post" in paths["/api/v1/contents/{content_id}/versions"]
    assert "get" in paths["/api/v1/contents/{content_id}/versions/{version_id}"]
    append = paths["/api/v1/contents/{content_id}/versions"]["post"]
    get_version = paths["/api/v1/contents/{content_id}/versions/{version_id}"]["get"]
    create = paths["/api/v1/contents"]["post"]
    get_content = paths["/api/v1/contents/{content_id}"]["get"]
    assert append["operationId"] == "content_version_append"
    assert get_version["operationId"] == "content_version_get"
    assert create["operationId"] == "content_create"
    dumped = canonical_openapi_json(schema)
    assert "HTTPValidationError" not in dumped
    assert "ContentVersionAppendRequest" in dumped
    assert "ContentVersionResponse" in dumped

    def header_names(operation: dict) -> set[str]:
        return {
            p["name"]
            for p in operation.get("parameters", [])
            if isinstance(p, dict) and p.get("in") == "header"
        }

    assert "Idempotency-Key" in header_names(create)
    assert "Idempotency-Key" in header_names(append)
    assert "If-Match" in header_names(append)
    assert "Idempotency-Key" not in header_names(get_content)
    assert "If-Match" not in header_names(get_content)
    assert "Idempotency-Key" not in header_names(get_version)
    assert "If-Match" not in header_names(get_version)
    for status in ("201", "400", "404", "409", "412", "422", "428", "500", "503"):
        assert status in append["responses"]

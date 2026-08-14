"""GCI-I09 OpenAPI operationId, headers, and snapshot equality for publish."""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest

from aieos.domains.content.application.catalog import StaticContentTypeCatalog
from aieos.domains.content.domain.schema import ContentSchemaRegistry
from aieos.platform.api.app import create_app
from aieos.platform.api.openapi import build_openapi, canonical_openapi_json
from tests.dbutil import REPO_ROOT
from tests.fakes import (
    AllowAssetCurrentGovernance,
    AllowAssetReferenceValidation,
    AllowPublicationAuthorization,
    AllowPublicationGovernance,
    AllowReviewAuthorization,
    AllowReviewCommentPolicy,
    StubSecurityContextResolver,
)

pytestmark = pytest.mark.gci_i09

SNAPSHOT = REPO_ROOT / "contracts" / "openapi" / "aieos-v1.json"
PUBLISH_PATH = "/api/v1/contents/{content_id}/actions/publish"


class _UnusedUowFactory:
    def __call__(self, execution_tenant_id):
        raise AssertionError("OpenAPI export must not touch persistence")


def _schema() -> dict:
    app = create_app(
        uow_factory=_UnusedUowFactory(),
        security_resolver=StubSecurityContextResolver(uuid4(), uuid4()),
        content_types=StaticContentTypeCatalog({"test.generic"}),
        cursor_signing_key=b"gci-i04-openapi-export-key",
        schema_registry=ContentSchemaRegistry(),
        idempotency_retention=timedelta(hours=24),
        review_authorization=AllowReviewAuthorization(),
        review_comment_policy=AllowReviewCommentPolicy(),
        publication_authorization=AllowPublicationAuthorization(),
        publication_governance=AllowPublicationGovernance(),
        asset_reference_validation=AllowAssetReferenceValidation(),
        asset_current_governance=AllowAssetCurrentGovernance(),
    )
    return build_openapi(app)


def _header_names(operation: dict) -> set[str]:
    return {
        p["name"]
        for p in operation.get("parameters", [])
        if isinstance(p, dict) and p.get("in") == "header"
    }


def _required_header_names(operation: dict) -> set[str]:
    return {
        p["name"]
        for p in operation.get("parameters", [])
        if isinstance(p, dict) and p.get("in") == "header" and p.get("required") is True
    }


def test_content_publish_operation_contract() -> None:
    schema = _schema()
    assert schema["openapi"].startswith("3.1")
    paths = schema["paths"]
    assert PUBLISH_PATH in paths
    assert "post" in paths[PUBLISH_PATH]
    operation = paths[PUBLISH_PATH]["post"]
    assert operation["operationId"] == "content_publish"

    headers = _header_names(operation)
    assert "If-Match" in headers
    assert "Idempotency-Key" in headers
    required = _required_header_names(operation)
    assert "If-Match" in required
    assert "Idempotency-Key" in required

    body_schema = operation["requestBody"]["content"]["application/json"]["schema"]
    if "$ref" in body_schema:
        ref = body_schema["$ref"].rsplit("/", 1)[-1]
        props = schema["components"]["schemas"][ref]["properties"]
    else:
        props = body_schema["properties"]
    assert set(props) == {"version_id"}

    statuses = set(operation["responses"])
    for status in (
        "200",
        "400",
        "401",
        "403",
        "404",
        "409",
        "412",
        "422",
        "428",
        "500",
        "503",
    ):
        assert status in statuses
    assert "application/problem+json" in operation["responses"]["412"]["content"]
    assert "application/problem+json" in operation["responses"]["428"]["content"]

    get_content = paths["/api/v1/contents/{content_id}"]["get"]
    list_contents = paths["/api/v1/contents"]["get"]
    get_version = paths["/api/v1/contents/{content_id}/versions/{version_id}"]["get"]
    for get_operation in (get_content, list_contents, get_version):
        get_headers = _header_names(get_operation)
        assert "If-Match" not in get_headers
        assert "Idempotency-Key" not in get_headers
        get_statuses = set(get_operation["responses"])
        assert "412" not in get_statuses
        assert "428" not in get_statuses

    dumped = canonical_openapi_json(schema)
    assert "HTTPValidationError" not in dumped
    assert "ProblemDetails" in dumped
    assert "PublicationResponse" in dumped
    assert "ContentPublishRequest" in dumped
    assert "/archive" not in dumped
    assert "version_asset_refs" not in dumped


def test_openapi_snapshot_regenerated_twice_identically() -> None:
    first = canonical_openapi_json(_schema())
    second = canonical_openapi_json(_schema())
    assert first == second
    assert SNAPSHOT.is_file()
    assert SNAPSHOT.read_text(encoding="utf-8") == first

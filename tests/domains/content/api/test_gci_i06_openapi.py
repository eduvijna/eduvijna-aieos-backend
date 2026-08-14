"""GCI-I06 OpenAPI operationIds, headers, and snapshot equality."""

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

pytestmark = pytest.mark.gci_i06

SNAPSHOT = REPO_ROOT / "contracts" / "openapi" / "aieos-v1.json"
REVIEW_PATHS = {
    "/api/v1/contents/{content_id}/versions/{version_id}/actions/submit-for-review": (
        "content_review_submit"
    ),
    "/api/v1/contents/{content_id}/versions/{version_id}/actions/approve": (
        "content_review_approve"
    ),
    "/api/v1/contents/{content_id}/versions/{version_id}/actions/request-changes": (
        "content_review_request_changes"
    ),
    "/api/v1/contents/{content_id}/versions/{version_id}/actions/reject": (
        "content_review_reject"
    ),
}


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


def test_four_review_operation_ids_and_required_headers() -> None:
    schema = _schema()
    assert schema["openapi"].startswith("3.1")
    paths = schema["paths"]
    for path, operation_id in REVIEW_PATHS.items():
        assert "post" in paths[path]
        operation = paths[path]["post"]
        assert operation["operationId"] == operation_id
        headers = _header_names(operation)
        assert "If-Match" in headers
        assert "Idempotency-Key" in headers
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
        problem = operation["responses"]["412"]
        assert "application/problem+json" in problem["content"]

    get_content = paths["/api/v1/contents/{content_id}"]["get"]
    list_contents = paths["/api/v1/contents"]["get"]
    get_version = paths["/api/v1/contents/{content_id}/versions/{version_id}"]["get"]
    for operation in (get_content, list_contents, get_version):
        headers = _header_names(operation)
        assert "If-Match" not in headers
        assert "Idempotency-Key" not in headers
        statuses = set(operation["responses"])
        assert "412" not in statuses
        assert "428" not in statuses

    dumped = canonical_openapi_json(schema)
    assert "HTTPValidationError" not in dumped
    assert "ProblemDetails" in dumped
    assert "ReviewSubmissionResponse" in dumped
    assert "ReviewDecisionResponse" in dumped
    assert "review-queue" not in dumped


def test_openapi_snapshot_regenerated_twice_identically() -> None:
    first = canonical_openapi_json(_schema())
    second = canonical_openapi_json(_schema())
    assert first == second
    assert SNAPSHOT.is_file()
    assert SNAPSHOT.read_text(encoding="utf-8") == first

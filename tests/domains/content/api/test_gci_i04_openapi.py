"""Deterministic OpenAPI 3.1 snapshot for GCI-I04."""

from __future__ import annotations

from uuid import uuid4

import pytest

from datetime import timedelta

from aieos.domains.content.application.catalog import StaticContentTypeCatalog
from aieos.domains.content.domain.schema import ContentSchemaRegistry
from aieos.platform.api.app import create_app
from aieos.platform.api.openapi import build_openapi, canonical_openapi_json
from tests.dbutil import REPO_ROOT
from tests.fakes import FixedPrincipalAuthenticator, StubSecurityContextResolver, AllowReviewAuthorization, AllowReviewCommentPolicy, AllowPublicationAuthorization, AllowPublicationGovernance, AllowAssetReferenceValidation, AllowAssetCurrentGovernance

pytestmark = pytest.mark.gci_i04

SNAPSHOT = REPO_ROOT / "contracts" / "openapi" / "aieos-v1.json"


class _UnusedUowFactory:
    def __call__(self, execution_tenant_id):
        raise AssertionError("OpenAPI export must not touch persistence")


def _schema() -> dict:
    app = create_app(
        uow_factory=_UnusedUowFactory(),
        request_identity_authenticator=FixedPrincipalAuthenticator(uuid4()),
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


def test_openapi_is_31_with_stable_operation_ids() -> None:
    schema = _schema()
    assert schema["openapi"].startswith("3.1")
    operation_ids = []
    paths = schema["paths"]
    assert "/api/v1/contents" in paths
    assert "/api/v1/contents/{content_id}" in paths
    assert "post" in paths["/api/v1/contents"]
    assert "get" in paths["/api/v1/contents"]
    assert "get" in paths["/api/v1/contents/{content_id}"]
    assert "post" not in paths["/api/v1/contents/{content_id}"]
    for path, item in paths.items():
        for method, operation in item.items():
            if method.startswith("x-") or not isinstance(operation, dict):
                continue
            operation_ids.append(operation.get("operationId"))
    assert "content_create" in operation_ids
    assert "content_get" in operation_ids
    assert "content_list" in operation_ids
    dumped = canonical_openapi_json(schema)
    assert "HTTPValidationError" not in dumped
    assert "X-AIEOS-Tenant-ID" in dumped
    assert "X-AIEOS-Correlation-ID" in dumped
    assert "ETag" in dumped
    assert "Location" in dumped
    assert "application/problem+json" in dumped
    assert "ProblemDetails" in dumped


def test_openapi_snapshot_is_deterministic_and_checked_in() -> None:
    first = canonical_openapi_json(_schema())
    second = canonical_openapi_json(_schema())
    assert first == second
    assert SNAPSHOT.is_file()
    assert SNAPSHOT.read_text(encoding="utf-8") == first

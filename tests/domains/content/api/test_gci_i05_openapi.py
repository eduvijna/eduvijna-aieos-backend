"""GCI-I05 OpenAPI operationIds, headers, and per-operation error statuses."""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest

from aieos.domains.content.application.catalog import StaticContentTypeCatalog
from aieos.domains.content.domain.schema import ContentSchemaRegistry
from aieos.platform.api.app import create_app
from aieos.platform.api.openapi import build_openapi, canonical_openapi_json
from tests.fakes import FixedPrincipalAuthenticator, StubSecurityContextResolver, AllowReviewAuthorization, AllowReviewCommentPolicy, AllowPublicationAuthorization, AllowPublicationGovernance, AllowAssetReferenceValidation, AllowAssetCurrentGovernance

pytestmark = pytest.mark.gci_i05


class _UnusedUowFactory:
    def __call__(self, execution_tenant_id):
        raise AssertionError("OpenAPI export must not touch persistence")


def _schema() -> dict:
    app = create_app(
        uow_factory=_UnusedUowFactory(),
        teaching_uow_factory=_UnusedUowFactory(),
        assessment_uow_factory=_UnusedUowFactory(),
        request_identity_authenticator=FixedPrincipalAuthenticator(uuid4()),
        security_resolver=StubSecurityContextResolver(uuid4(), uuid4()),
        content_types=StaticContentTypeCatalog({"test.generic"}),
        cursor_signing_key=b"gci-i05-openapi-export-key",
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
    list_contents = paths["/api/v1/contents"]["get"]
    assert append["operationId"] == "content_version_append"
    assert get_version["operationId"] == "content_version_get"
    assert create["operationId"] == "content_create"
    dumped = canonical_openapi_json(schema)
    assert "HTTPValidationError" not in dumped
    assert "ContentVersionAppendRequest" in dumped
    assert "ContentVersionResponse" in dumped

    assert "Idempotency-Key" in _header_names(create)
    assert "Idempotency-Key" in _header_names(append)
    assert "If-Match" in _header_names(append)
    assert "If-Match" not in _header_names(create)
    for operation in (get_content, list_contents, get_version):
        assert "Idempotency-Key" not in _header_names(operation)
        assert "If-Match" not in _header_names(operation)

    create_statuses = set(create["responses"])
    get_statuses = set(get_content["responses"])
    list_statuses = set(list_contents["responses"])
    get_version_statuses = set(get_version["responses"])
    append_statuses = set(append["responses"])

    for status in ("400", "401", "403", "409", "422", "500", "503"):
        assert status in create_statuses
    assert "412" not in create_statuses
    assert "428" not in create_statuses

    assert "409" not in get_statuses
    assert "412" not in get_statuses
    assert "428" not in get_statuses

    assert "409" not in list_statuses
    assert "412" not in list_statuses
    assert "428" not in list_statuses

    assert "409" not in get_version_statuses
    assert "412" not in get_version_statuses
    assert "428" not in get_version_statuses

    for status in ("400", "401", "403", "404", "409", "412", "422", "428", "500", "503"):
        assert status in append_statuses

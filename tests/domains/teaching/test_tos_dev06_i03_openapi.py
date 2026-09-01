"""TOS-DEV06-I03 — OpenAPI contract for TeachingAssignment routes."""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest

from aieos.domains.content.application.catalog import StaticContentTypeCatalog
from aieos.domains.teaching.application.school_context import AssignableClassRef
from aieos.platform.api.app import create_app
from aieos.platform.api.openapi import build_openapi, canonical_openapi_json
from tests.fakes import (
    AllowAssetCurrentGovernance,
    AllowAssetReferenceValidation,
    AllowPublicationAuthorization,
    AllowPublicationGovernance,
    AllowReviewAuthorization,
    AllowReviewCommentPolicy,
    FixedPrincipalAuthenticator,
    StubSecurityContextResolver,
    make_test_schema_registry,
)

pytestmark = pytest.mark.tos_dev06_i03

CREATE_PATH = "/api/v1/teaching/assignments"


class _UnusedUowFactory:
    def __call__(self, execution_tenant_id):
        raise AssertionError("OpenAPI export must not touch persistence")


class _Reader:
    def list_assignable_classes(self, tenant_id, teacher_principal_id):
        return (AssignableClassRef(class_ref="class-5a", display_label="Grade 5A"),)


def _schema() -> dict:
    app = create_app(
        uow_factory=_UnusedUowFactory(),  # type: ignore[arg-type]
        teaching_uow_factory=_UnusedUowFactory(),  # type: ignore[arg-type]
        request_identity_authenticator=FixedPrincipalAuthenticator(uuid4()),
        security_resolver=StubSecurityContextResolver(uuid4(), uuid4()),
        content_types=StaticContentTypeCatalog({"test.generic", "worksheet"}),
        cursor_signing_key=b"tos-dev06-i03-openapi-key",
        schema_registry=make_test_schema_registry(),
        idempotency_retention=timedelta(hours=24),
        review_authorization=AllowReviewAuthorization(),
        review_comment_policy=AllowReviewCommentPolicy(),
        publication_authorization=AllowPublicationAuthorization(),
        publication_governance=AllowPublicationGovernance(),
        asset_reference_validation=AllowAssetReferenceValidation(),
        asset_current_governance=AllowAssetCurrentGovernance(),
        school_context_class_reader=_Reader(),
    )
    return build_openapi(app)


def test_teaching_assignment_create_operation_contract() -> None:
    schema = _schema()
    operation = schema["paths"][CREATE_PATH]["post"]
    assert operation["operationId"] == "teaching_assignment_create"
    header_names = {
        p["name"]
        for p in operation.get("parameters", [])
        if p.get("in") == "header"
    }
    assert "Idempotency-Key" in header_names


def test_openapi_export_is_deterministic() -> None:
    first = canonical_openapi_json(_schema())
    second = canonical_openapi_json(_schema())
    assert first == second

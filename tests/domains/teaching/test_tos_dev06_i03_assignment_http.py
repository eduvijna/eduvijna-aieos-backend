"""TOS-DEV06-I03 — TeachingAssignment HTTP contract tests."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from aieos.domains.content.application.catalog import StaticContentTypeCatalog
from aieos.domains.teaching.application.school_context import AssignableClassRef
from aieos.platform.api.app import create_app
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
CURSOR_KEY = b"tos-dev06-i03-test-cursor-key"
IDEMPOTENCY_RETENTION = timedelta(hours=24)


class _UnusedUowFactory:
    def __call__(self, execution_tenant_id):
        raise AssertionError("HTTP contract test must not open UoW")


class _Reader:
    def list_assignable_classes(
        self, tenant_id: UUID, teacher_principal_id: UUID
    ) -> tuple[AssignableClassRef, ...]:
        return (
            AssignableClassRef(class_ref="class-5a", display_label="Grade 5A"),
        )


def _client(tenant_id: UUID, principal_id: UUID) -> TestClient:
    app = create_app(
        uow_factory=_UnusedUowFactory(),  # type: ignore[arg-type]
        teaching_uow_factory=_UnusedUowFactory(),  # type: ignore[arg-type]
        request_identity_authenticator=FixedPrincipalAuthenticator(principal_id),
        security_resolver=StubSecurityContextResolver(tenant_id, principal_id),
        content_types=StaticContentTypeCatalog({"test.generic", "worksheet"}),
        cursor_signing_key=CURSOR_KEY,
        schema_registry=make_test_schema_registry(),
        idempotency_retention=IDEMPOTENCY_RETENTION,
        review_authorization=AllowReviewAuthorization(),
        review_comment_policy=AllowReviewCommentPolicy(),
        publication_authorization=AllowPublicationAuthorization(),
        publication_governance=AllowPublicationGovernance(),
        asset_reference_validation=AllowAssetReferenceValidation(),
        asset_current_governance=AllowAssetCurrentGovernance(),
        school_context_class_reader=_Reader(),
    )
    return TestClient(app)


def test_create_requires_idempotency_key() -> None:
    tenant_id = uuid4()
    principal_id = uuid4()
    client = _client(tenant_id, principal_id)
    response = client.post(
        CREATE_PATH,
        headers={"X-AIEOS-Tenant-ID": str(tenant_id)},
        json={
            "content_id": str(uuid4()),
            "content_version_id": str(uuid4()),
            "class_ref": "class-5a",
        },
    )
    assert response.status_code == 400
    assert response.json()["code"] == "idempotency_key_required"


def test_create_without_school_context_returns_503() -> None:
    tenant_id = uuid4()
    principal_id = uuid4()
    app = create_app(
        uow_factory=_UnusedUowFactory(),  # type: ignore[arg-type]
        teaching_uow_factory=_UnusedUowFactory(),  # type: ignore[arg-type]
        request_identity_authenticator=FixedPrincipalAuthenticator(principal_id),
        security_resolver=StubSecurityContextResolver(tenant_id, principal_id),
        content_types=StaticContentTypeCatalog({"test.generic"}),
        cursor_signing_key=CURSOR_KEY,
        schema_registry=make_test_schema_registry(),
        idempotency_retention=IDEMPOTENCY_RETENTION,
        review_authorization=AllowReviewAuthorization(),
        review_comment_policy=AllowReviewCommentPolicy(),
        publication_authorization=AllowPublicationAuthorization(),
        publication_governance=AllowPublicationGovernance(),
        asset_reference_validation=AllowAssetReferenceValidation(),
        asset_current_governance=AllowAssetCurrentGovernance(),
        school_context_class_reader=None,
    )
    client = TestClient(app)
    response = client.post(
        CREATE_PATH,
        headers={
            "X-AIEOS-Tenant-ID": str(tenant_id),
            "Idempotency-Key": "create-without-school-context",
        },
        json={
            "content_id": str(uuid4()),
            "content_version_id": str(uuid4()),
            "class_ref": "class-5a",
        },
    )
    assert response.status_code == 503

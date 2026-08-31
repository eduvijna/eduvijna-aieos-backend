"""TOS-DEV06-I01 — School Context ClassRef HTTP contract tests."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from aieos.domains.content.application.catalog import StaticContentTypeCatalog
from aieos.domains.teaching.application.errors import SchoolContextUnavailable
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

pytestmark = pytest.mark.tos_dev06_i01

PATH = "/api/v1/teacher-os/school-context/classes"
CURSOR_KEY = b"tos-dev06-i01-test-cursor-key"
IDEMPOTENCY_RETENTION = timedelta(hours=24)


class _UnusedUowFactory:
    def __call__(self, execution_tenant_id):
        raise AssertionError("School Context read must not open a Teaching/Content UoW")


class _CountingReader:
    def __init__(
        self,
        result: tuple[AssignableClassRef, ...] = (),
        *,
        exc: BaseException | None = None,
    ) -> None:
        self.result = result
        self.exc = exc
        self.call_count = 0
        self.calls: list[tuple[UUID, UUID]] = []

    def list_assignable_classes(
        self, tenant_id: UUID, teacher_principal_id: UUID
    ) -> tuple[AssignableClassRef, ...]:
        self.call_count += 1
        self.calls.append((tenant_id, teacher_principal_id))
        if self.exc is not None:
            raise self.exc
        return self.result


def _headers(tenant_id: UUID) -> dict[str, str]:
    return {"X-AIEOS-Tenant-ID": str(tenant_id)}


def _build_client(
    tenant_id: UUID,
    principal_id: UUID,
    *,
    reader: object | None = ...,
    authenticator=None,
    security_resolver=None,
) -> tuple[TestClient, object | None]:
    if reader is ...:
        reader = _CountingReader(
            (
                AssignableClassRef(
                    class_ref="class-5a", display_label="Grade 5A"
                ),
                AssignableClassRef(
                    class_ref="class-5b", display_label="Grade 5B"
                ),
            )
        )
    app = create_app(
        uow_factory=_UnusedUowFactory(),  # type: ignore[arg-type]
        teaching_uow_factory=_UnusedUowFactory(),  # type: ignore[arg-type]
        request_identity_authenticator=authenticator
        or FixedPrincipalAuthenticator(principal_id),
        security_resolver=security_resolver
        or StubSecurityContextResolver(tenant_id, principal_id),
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
        school_context_class_reader=reader,  # type: ignore[arg-type]
    )
    return TestClient(app, raise_server_exceptions=False), reader


class TestSchoolContextClassesHttp:
    def test_authenticated_teacher_returns_200_opaque_refs(self) -> None:
        tenant_id = uuid4()
        principal_id = uuid4()
        client, reader = _build_client(tenant_id, principal_id)

        response = client.get(PATH, headers=_headers(tenant_id))

        assert response.status_code == 200
        body = response.json()
        assert body == {
            "items": [
                {"class_ref": "class-5a", "display_label": "Grade 5A"},
                {"class_ref": "class-5b", "display_label": "Grade 5B"},
            ]
        }
        assert "tenant_id" not in body["items"][0]
        assert "principal_id" not in body["items"][0]
        assert reader.call_count == 1
        assert reader.calls == [(tenant_id, principal_id)]

    def test_zero_permitted_classes_returns_200_empty(self) -> None:
        tenant_id = uuid4()
        principal_id = uuid4()
        client, reader = _build_client(
            tenant_id,
            principal_id,
            reader=_CountingReader(()),
        )

        response = client.get(PATH, headers=_headers(tenant_id))

        assert response.status_code == 200
        assert response.json() == {"items": []}
        assert reader.call_count == 1

    def test_provider_unavailable_returns_503_sanitized(self) -> None:
        tenant_id = uuid4()
        principal_id = uuid4()
        secret = "erp://secret-url?token=abc"
        client, reader = _build_client(
            tenant_id,
            principal_id,
            reader=_CountingReader(exc=RuntimeError(secret)),
        )

        response = client.get(PATH, headers=_headers(tenant_id))

        assert response.status_code == 503
        body = response.json()
        assert body["code"] == "school_context_unavailable"
        assert secret not in response.text
        assert "erp://" not in response.text
        assert reader.call_count == 1

    def test_explicit_school_context_unavailable_is_503(self) -> None:
        tenant_id = uuid4()
        principal_id = uuid4()
        client, _ = _build_client(
            tenant_id,
            principal_id,
            reader=_CountingReader(exc=SchoolContextUnavailable("provider down")),
        )

        response = client.get(PATH, headers=_headers(tenant_id))

        assert response.status_code == 503
        assert response.json()["code"] == "school_context_unavailable"

    def test_no_provider_composition_returns_503(self) -> None:
        tenant_id = uuid4()
        principal_id = uuid4()
        client, _ = _build_client(tenant_id, principal_id, reader=None)

        response = client.get(PATH, headers=_headers(tenant_id))

        assert response.status_code == 503
        assert response.json()["code"] == "school_context_unavailable"

    def test_unauthenticated_returns_401_provider_calls_zero(self) -> None:
        tenant_id = uuid4()
        principal_id = uuid4()
        reader = _CountingReader(
            (
                AssignableClassRef(
                    class_ref="class-5a", display_label="Grade 5A"
                ),
            )
        )
        client, _ = _build_client(
            tenant_id,
            principal_id,
            reader=reader,
            authenticator=FixedPrincipalAuthenticator(
                principal_id, unauthenticated=True
            ),
        )

        response = client.get(PATH, headers=_headers(tenant_id))

        assert response.status_code == 401
        assert response.json()["code"] == "unauthenticated"
        assert reader.call_count == 0

    def test_unauthorized_tenant_returns_403_provider_calls_zero(self) -> None:
        authorized_tenant = uuid4()
        other_tenant = uuid4()
        principal_id = uuid4()
        reader = _CountingReader(
            (
                AssignableClassRef(
                    class_ref="class-5a", display_label="Grade 5A"
                ),
            )
        )
        client, _ = _build_client(
            authorized_tenant,
            principal_id,
            reader=reader,
        )

        response = client.get(PATH, headers=_headers(other_tenant))

        assert response.status_code == 403
        assert response.json()["code"] == "forbidden"
        assert reader.call_count == 0

    def test_spoofed_actor_headers_do_not_alter_principal(self) -> None:
        tenant_id = uuid4()
        principal_id = uuid4()
        spoofed = uuid4()
        client, reader = _build_client(tenant_id, principal_id)

        response = client.get(
            PATH,
            headers={
                **_headers(tenant_id),
                "X-Actor-Id": str(spoofed),
                "X-Teacher-Principal-Id": str(spoofed),
                "X-AIEOS-Principal-ID": str(spoofed),
                "X-Role": "admin",
                "X-Capability": "school.admin",
            },
        )

        assert response.status_code == 200
        assert reader.calls == [(tenant_id, principal_id)]
        assert reader.calls[0][1] != spoofed

    def test_request_body_cannot_supply_authoritative_principal(self) -> None:
        tenant_id = uuid4()
        principal_id = uuid4()
        spoofed = uuid4()
        client, reader = _build_client(tenant_id, principal_id)

        response = client.request(
            "GET",
            PATH,
            headers=_headers(tenant_id),
            json={
                "teacher_principal_id": str(spoofed),
                "tenant_id": str(uuid4()),
            },
        )

        assert response.status_code == 200
        assert reader.calls == [(tenant_id, principal_id)]

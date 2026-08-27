"""PED-I07 trusted request identity and current-tenant SecurityContext behavior."""

from __future__ import annotations

import json
import uuid
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from aieos.domains.content.application.catalog import StaticContentTypeCatalog
from aieos.platform.api.app import create_app
from aieos.platform.runtime.activation import (
    MutationActivationDecision,
    MutationActivationStatus,
    install_mutation_activation_interlock,
)
from aieos.platform.runtime.health import register_operational_health_routes
from aieos.platform.runtime.models import DeploymentEnvironment, ReleaseIdentity
from aieos.platform.runtime.readiness import ReadinessCode, ReadinessResult
from aieos.platform.security.authority import CurrentAuthoritySecurityContextResolver
from aieos.platform.security.context import TrustedSecurityContext
from aieos.platform.security.identity import TrustedRequestIdentity
from tests.fakes import (
    AllowAssetCurrentGovernance,
    AllowAssetReferenceValidation,
    AllowPublicationAuthorization,
    AllowPublicationGovernance,
    AllowReviewAuthorization,
    AllowReviewCommentPolicy,
    FixedPrincipalAuthenticator,
    IDEMPOTENCY_RETENTION,
    MutableCurrentTenantAccessAuthority,
    RecordingUowFactory,
    make_test_schema_registry,
)

pytestmark = pytest.mark.ped_i07

CREATE_BODY = {
    "content_type": "test.generic",
    "title": "Title",
    "description": "Description",
    "locale": "en-IN",
}
SECRET_SENTINEL = "SECRET_AUTH_PROVIDER_TOKEN_xyzzy"
LEAK_NEEDLES = (
    SECRET_SENTINEL,
    "Traceback",
    "sqlalchemy",
    "password",
    "Bearer ",
)


class _EnabledMutationGate:
    def check(self) -> MutationActivationDecision:
        return MutationActivationDecision(True, MutationActivationStatus.ENABLED)


class _ReadyProbe:
    def check(self) -> ReadinessResult:
        return ReadinessResult(True, ReadinessCode.READY)


def _headers(tenant_id: UUID, **extra: str) -> dict[str, str]:
    headers = {"X-AIEOS-Tenant-ID": str(tenant_id), **extra}
    if "Idempotency-Key" not in headers:
        headers["Idempotency-Key"] = f"test-{uuid.uuid7()}"
    return headers


def _assert_problem(response, *, status: int, code: str) -> dict:
    assert response.status_code == status, response.text
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["code"] == code
    assert body["status"] == status
    assert body["instance"]
    assert body["request_id"]
    assert body["correlation_id"]
    UUID(body["request_id"])
    UUID(body["correlation_id"])
    blob = json.dumps(body) + response.text
    for needle in LEAK_NEEDLES:
        assert needle.lower() not in blob.lower()
    return body


def _app(
    *,
    authenticator,
    authority: MutableCurrentTenantAccessAuthority,
    uow_factory=None,
    enable_mutations: bool = False,
):
    factory = uow_factory or RecordingUowFactory()
    app = create_app(
        uow_factory=factory,
        teaching_uow_factory=factory,
        request_identity_authenticator=authenticator,
        security_resolver=CurrentAuthoritySecurityContextResolver(authority),
        content_types=StaticContentTypeCatalog({"test.generic"}),
        cursor_signing_key=b"ped-i07-test-cursor-signing-key",
        schema_registry=make_test_schema_registry(),
        idempotency_retention=IDEMPOTENCY_RETENTION,
        review_authorization=AllowReviewAuthorization(),
        review_comment_policy=AllowReviewCommentPolicy(),
        publication_authorization=AllowPublicationAuthorization(),
        publication_governance=AllowPublicationGovernance(),
        asset_reference_validation=AllowAssetReferenceValidation(),
        asset_current_governance=AllowAssetCurrentGovernance(),
    )
    if enable_mutations:
        install_mutation_activation_interlock(app, _EnabledMutationGate())
    return app, factory


class TestHappyPathAndSpoofing:
    def test_authenticated_happy_path_reaches_uow(self) -> None:
        principal = uuid.uuid7()
        tenant = uuid.uuid7()
        authority = MutableCurrentTenantAccessAuthority()
        authority.grant(principal, tenant)
        factory = RecordingUowFactory()
        app, factory = _app(
            authenticator=FixedPrincipalAuthenticator(principal),
            authority=authority,
            uow_factory=factory,
            enable_mutations=True,
        )
        client = TestClient(app, raise_server_exceptions=False)
        listed = client.get("/api/v1/contents", headers=_headers(tenant))
        # Auth + current tenant authority succeeded; UoW opened (factory raises).
        assert factory.calls == 1
        assert factory.tenants == [tenant]
        assert authority.calls == [(principal, tenant)]
        assert listed.status_code == 500
        created = client.post(
            "/api/v1/contents", json=CREATE_BODY, headers=_headers(tenant)
        )
        assert factory.calls == 2
        assert factory.tenants[-1] == tenant
        assert created.status_code == 500
        ctx = CurrentAuthoritySecurityContextResolver(authority).resolve(
            identity=TrustedRequestIdentity(principal_id=principal),
            requested_tenant_id=tenant,
        )
        assert ctx == TrustedSecurityContext(tenant_id=tenant, principal_id=principal)

    def test_spoofed_principal_headers_ignored(self) -> None:
        principal_a = uuid.uuid7()
        principal_b = uuid.uuid7()
        tenant = uuid.uuid7()
        authority = MutableCurrentTenantAccessAuthority()
        authority.grant(principal_a, tenant)
        factory = RecordingUowFactory()
        app, factory = _app(
            authenticator=FixedPrincipalAuthenticator(principal_a),
            authority=authority,
            uow_factory=factory,
            enable_mutations=True,
        )
        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(
            "/api/v1/contents",
            json=CREATE_BODY,
            headers=_headers(
                tenant,
                **{
                    "X-AIEOS-Principal-ID": str(principal_b),
                    "X-User-ID": str(principal_b),
                    "X-Actor-ID": str(principal_b),
                    "X-Roles": "admin",
                    "X-Permissions": "content.publish",
                    "X-Capabilities": "content.review.decide",
                    "X-Admin": "true",
                    "X-Superuser": "1",
                },
            ),
        )
        assert factory.calls == 1
        assert authority.calls == [(principal_a, tenant)]
        assert (principal_b, tenant) not in authority.calls
        assert response.status_code == 500

    def test_spoofed_tenant_denied_before_uow(self) -> None:
        principal = uuid.uuid7()
        tenant_ok = uuid.uuid7()
        tenant_other = uuid.uuid7()
        authority = MutableCurrentTenantAccessAuthority()
        authority.grant(principal, tenant_ok)
        factory = RecordingUowFactory()
        app, factory = _app(
            authenticator=FixedPrincipalAuthenticator(principal),
            authority=authority,
            uow_factory=factory,
            enable_mutations=True,
        )
        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(
            "/api/v1/contents",
            json={**CREATE_BODY, "tenant_id": str(tenant_ok)},
            headers=_headers(tenant_other),
        )
        _assert_problem(response, status=403, code="forbidden")
        assert factory.calls == 0
        listed = client.get(
            "/api/v1/contents",
            params={"tenant_id": str(tenant_ok)},
            headers=_headers(tenant_other),
        )
        _assert_problem(listed, status=403, code="forbidden")
        assert factory.calls == 0


class TestFailureSafety:
    def test_unauthenticated_read_zero_uow(self) -> None:
        principal = uuid.uuid7()
        tenant = uuid.uuid7()
        authority = MutableCurrentTenantAccessAuthority()
        authority.grant(principal, tenant)
        factory = RecordingUowFactory()
        app, factory = _app(
            authenticator=FixedPrincipalAuthenticator(principal, unauthenticated=True),
            authority=authority,
            uow_factory=factory,
        )
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/v1/contents", headers=_headers(tenant))
        _assert_problem(response, status=401, code="unauthenticated")
        assert factory.calls == 0

    def test_missing_tenant_read_zero_uow(self) -> None:
        principal = uuid.uuid7()
        tenant = uuid.uuid7()
        authority = MutableCurrentTenantAccessAuthority()
        authority.grant(principal, tenant)
        factory = RecordingUowFactory()
        app, factory = _app(
            authenticator=FixedPrincipalAuthenticator(principal),
            authority=authority,
            uow_factory=factory,
        )
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/v1/contents")
        _assert_problem(response, status=401, code="unauthenticated")
        assert factory.calls == 0

    def test_unauthorized_tenant_read_zero_uow(self) -> None:
        principal = uuid.uuid7()
        tenant = uuid.uuid7()
        other = uuid.uuid7()
        authority = MutableCurrentTenantAccessAuthority()
        authority.grant(principal, tenant)
        factory = RecordingUowFactory()
        app, factory = _app(
            authenticator=FixedPrincipalAuthenticator(principal),
            authority=authority,
            uow_factory=factory,
        )
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/v1/contents", headers=_headers(other))
        _assert_problem(response, status=403, code="forbidden")
        assert factory.calls == 0

    def test_mutation_auth_failures_zero_uow(self) -> None:
        principal = uuid.uuid7()
        tenant = uuid.uuid7()
        authority = MutableCurrentTenantAccessAuthority()
        authority.grant(principal, tenant)

        for authenticator, status, code in (
            (
                FixedPrincipalAuthenticator(principal, unauthenticated=True),
                401,
                "unauthenticated",
            ),
            (
                FixedPrincipalAuthenticator(
                    principal, unavailable=True, unavailable_secret=SECRET_SENTINEL
                ),
                503,
                "authentication_unavailable",
            ),
        ):
            factory = RecordingUowFactory()
            app, factory = _app(
                authenticator=authenticator,
                authority=authority,
                uow_factory=factory,
                enable_mutations=True,
            )
            client = TestClient(app, raise_server_exceptions=False)
            response = client.post(
                "/api/v1/contents", json=CREATE_BODY, headers=_headers(tenant)
            )
            _assert_problem(response, status=status, code=code)
            assert factory.calls == 0

        deny_authority = MutableCurrentTenantAccessAuthority()
        factory = RecordingUowFactory()
        app, factory = _app(
            authenticator=FixedPrincipalAuthenticator(principal),
            authority=deny_authority,
            uow_factory=factory,
            enable_mutations=True,
        )
        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(
            "/api/v1/contents", json=CREATE_BODY, headers=_headers(tenant)
        )
        _assert_problem(response, status=403, code="forbidden")
        assert factory.calls == 0

        broken = MutableCurrentTenantAccessAuthority(
            unavailable=True, unavailable_secret=SECRET_SENTINEL
        )
        factory = RecordingUowFactory()
        app, factory = _app(
            authenticator=FixedPrincipalAuthenticator(principal),
            authority=broken,
            uow_factory=factory,
            enable_mutations=True,
        )
        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(
            "/api/v1/contents", json=CREATE_BODY, headers=_headers(tenant)
        )
        _assert_problem(response, status=503, code="authorization_unavailable")
        assert factory.calls == 0


class TestRevocationAndSuspension:
    def test_revocation_between_requests(self) -> None:
        principal = uuid.uuid7()
        tenant = uuid.uuid7()
        authority = MutableCurrentTenantAccessAuthority()
        authority.grant(principal, tenant)
        factory = RecordingUowFactory()
        app, factory = _app(
            authenticator=FixedPrincipalAuthenticator(principal),
            authority=authority,
            uow_factory=factory,
            enable_mutations=True,
        )
        client = TestClient(app, raise_server_exceptions=False)
        first = client.get("/api/v1/contents", headers=_headers(tenant))
        assert factory.calls == 1
        assert first.status_code == 500
        authority.revoke(principal, tenant)
        second = client.get("/api/v1/contents", headers=_headers(tenant))
        _assert_problem(second, status=403, code="forbidden")
        assert factory.calls == 1

    def test_suspended_tenant_denied(self) -> None:
        from aieos.platform.security.context import UnauthorizedError

        principal = uuid.uuid7()
        tenant = uuid.uuid7()
        authority = MutableCurrentTenantAccessAuthority()
        authority.grant(principal, tenant)
        resolver = CurrentAuthoritySecurityContextResolver(authority)
        identity = TrustedRequestIdentity(principal_id=principal)
        ctx = resolver.resolve(identity=identity, requested_tenant_id=tenant)
        assert ctx == TrustedSecurityContext(tenant_id=tenant, principal_id=principal)

        authority.suspend(tenant)
        with pytest.raises(UnauthorizedError):
            resolver.resolve(identity=identity, requested_tenant_id=tenant)

        factory = RecordingUowFactory()
        app, factory = _app(
            authenticator=FixedPrincipalAuthenticator(principal),
            authority=authority,
            uow_factory=factory,
        )
        client = TestClient(app, raise_server_exceptions=False)
        denied = client.get("/api/v1/contents", headers=_headers(tenant))
        _assert_problem(denied, status=403, code="forbidden")
        assert factory.calls == 0


class TestUnavailableSanitization:
    def test_authenticator_unavailable_sanitized(self) -> None:
        principal = uuid.uuid7()
        tenant = uuid.uuid7()
        authority = MutableCurrentTenantAccessAuthority()
        authority.grant(principal, tenant)
        factory = RecordingUowFactory()
        app, factory = _app(
            authenticator=FixedPrincipalAuthenticator(
                principal, unavailable=True, unavailable_secret=SECRET_SENTINEL
            ),
            authority=authority,
            uow_factory=factory,
        )
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/v1/contents", headers=_headers(tenant))
        body = _assert_problem(
            response, status=503, code="authentication_unavailable"
        )
        assert "X-AIEOS-Request-ID" in response.headers
        assert response.headers["X-AIEOS-Request-ID"] == body["request_id"]
        assert factory.calls == 0

    def test_authority_unavailable_sanitized(self) -> None:
        principal = uuid.uuid7()
        tenant = uuid.uuid7()
        authority = MutableCurrentTenantAccessAuthority(
            unavailable=True, unavailable_secret=SECRET_SENTINEL
        )
        factory = RecordingUowFactory()
        app, factory = _app(
            authenticator=FixedPrincipalAuthenticator(principal),
            authority=authority,
            uow_factory=factory,
        )
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/v1/contents", headers=_headers(tenant))
        body = _assert_problem(
            response, status=503, code="authorization_unavailable"
        )
        assert body["instance"] == "/api/v1/contents"
        assert factory.calls == 0


class TestHealthIndependence:
    def test_livez_readyz_without_auth_or_tenant(self) -> None:
        principal = uuid.uuid7()
        authority = MutableCurrentTenantAccessAuthority()
        factory = RecordingUowFactory()
        app, factory = _app(
            authenticator=FixedPrincipalAuthenticator(principal, unauthenticated=True),
            authority=authority,
            uow_factory=factory,
        )
        app.state.release_identity = ReleaseIdentity(
            application_version="0.1.0",
            git_sha="a" * 40,
            build_id="b",
            artifact_digest="sha256:" + ("c" * 64),
        )
        app.state.deployment_environment = DeploymentEnvironment.PRODUCTION
        app.state.readiness_probe = _ReadyProbe()
        register_operational_health_routes(app)
        client = TestClient(app, raise_server_exceptions=False)
        livez = client.get("/livez")
        assert livez.status_code == 200
        readyz = client.get("/readyz")
        assert readyz.status_code == 200
        assert factory.calls == 0

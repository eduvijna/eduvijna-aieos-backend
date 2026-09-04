"""PED-I09 trust-chain HTTP adversarial tests with Authorization Kernel."""

from __future__ import annotations

import json
import time
import uuid
from typing import Any
from uuid import UUID

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jwt import PyJWK
from sqlalchemy import create_engine

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
from aieos.platform.security.auth_config import (
    AIEOS_PRINCIPAL_ID_CLAIM,
    AuthRuntimeConfig,
)
from aieos.platform.security.authority import CurrentAuthoritySecurityContextResolver
from aieos.domains.content.application.ports import (
    CONTENT_PUBLISH,
    CONTENT_REVIEW_SUBMIT,
)
from aieos.platform.security.authorization import (
    AIEOS_CONTENT_CAPABILITIES,
    AuthorizationKernel,
    KernelCurrentTenantAccessAuthority,
    KernelPublicationAuthorization,
    KernelReviewAuthorization,
)
from aieos.platform.security.authorization.decisions import (
    MembershipStatus,
    PrincipalStatus,
    TenantStatus,
)
from aieos.platform.security.jwt_bearer import JwtBearerRequestIdentityAuthenticator
from tests.fakes import (
    AllowAssetCurrentGovernance,
    AllowAssetReferenceValidation,
    AllowPublicationGovernance,
    AllowReviewCommentPolicy,
    IDEMPOTENCY_RETENTION,
    MutableCurrentTenantAccessAuthority,
    RecordingUowFactory,
    make_test_schema_registry,
)
from tests.platform.security.authorization.helpers import (
    revoke_grant,
    revoke_membership,
    seed_active_authority,
    seed_principal,
    seed_tenant,
)

pytestmark = pytest.mark.ped_i09

ISSUER = "https://issuer.example.test/"
AUDIENCE = "aieos-api"
JWKS_URI = "https://issuer.example.test/.well-known/jwks.json"
KID = "ped-i09-test-key"
SECRET_SENTINEL = "SECRET_AUTHZ_PROVIDER_xyzzy"
LEAK_NEEDLES = (
    SECRET_SENTINEL,
    "Traceback",
    "sqlalchemy",
    "password",
    "psycopg",
    "BEGIN RSA",
)


class _EnabledMutationGate:
    def check(self) -> MutationActivationDecision:
        return MutationActivationDecision(True, MutationActivationStatus.ENABLED)


class _ReadyProbe:
    def check(self) -> ReadinessResult:
        return ReadinessResult(True, ReadinessCode.READY)


class _StaticJwkClient:
    def __init__(self, public_jwk: dict[str, Any]) -> None:
        self._key = PyJWK.from_dict(public_jwk)

    def get_signing_key_from_jwt(self, token: str) -> PyJWK:
        return self._key


@pytest.fixture(scope="module")
def rsa_material() -> tuple[Any, dict[str, Any]]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_numbers = private_key.public_key().public_numbers()
    import base64

    def _b64u(value: int, *, length: int) -> str:
        raw = value.to_bytes(length, "big")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    n_len = (public_numbers.n.bit_length() + 7) // 8
    public_jwk = {
        "kty": "RSA",
        "kid": KID,
        "use": "sig",
        "alg": "RS256",
        "n": _b64u(public_numbers.n, length=n_len),
        "e": _b64u(public_numbers.e, length=3),
    }
    return private_key, public_jwk


def _now() -> int:
    return int(time.time())


def _mint(
    private_key: Any,
    *,
    principal_id: UUID,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "idp-subject-1",
        "client_id": "aieos-client",
        "jti": str(uuid.uuid4()),
        "iat": _now(),
        "exp": _now() + 3600,
        AIEOS_PRINCIPAL_ID_CLAIM: str(principal_id),
    }
    if extra_claims:
        claims.update(extra_claims)
    return jwt.encode(
        claims,
        private_key,
        algorithm="RS256",
        headers={"kid": KID, "typ": "at+jwt"},
    )


def _headers(tenant_id: UUID, token: str | None = None, **extra: str) -> dict[str, str]:
    headers = {"X-AIEOS-Tenant-ID": str(tenant_id), **extra}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if "Idempotency-Key" not in headers:
        headers["Idempotency-Key"] = f"test-{uuid.uuid7()}"
    return headers


def _assert_problem(response, *, status: int, code: str) -> dict:
    assert response.status_code == status, response.text
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["code"] == code
    assert body["status"] == status
    blob = json.dumps(body) + response.text
    for needle in LEAK_NEEDLES:
        assert needle.lower() not in blob.lower()
    return body


def _authenticator(public_jwk: dict[str, Any]):
    config = AuthRuntimeConfig(issuer=ISSUER, audience=AUDIENCE, jwks_uri=JWKS_URI)
    return JwtBearerRequestIdentityAuthenticator(
        config, jwk_client=_StaticJwkClient(public_jwk)
    )


def _kernel(engine) -> AuthorizationKernel:
    return AuthorizationKernel(engine, known_capabilities=AIEOS_CONTENT_CAPABILITIES)


def _app(
    *,
    authenticator,
    authority,
    review_authorization=None,
    publication_authorization=None,
    uow_factory=None,
    enable_mutations: bool = False,
    with_health: bool = False,
):
    from tests.fakes import (
        AllowPublicationAuthorization,
        AllowReviewAuthorization,
    )

    factory = uow_factory or RecordingUowFactory()
    app = create_app(
        uow_factory=factory,
        teaching_uow_factory=factory,
        assessment_uow_factory=factory,
        request_identity_authenticator=authenticator,
        security_resolver=CurrentAuthoritySecurityContextResolver(authority),
        content_types=StaticContentTypeCatalog({"test.generic"}),
        cursor_signing_key=b"ped-i09-test-cursor-signing-key",
        schema_registry=make_test_schema_registry(),
        idempotency_retention=IDEMPOTENCY_RETENTION,
        review_authorization=review_authorization or AllowReviewAuthorization(),
        review_comment_policy=AllowReviewCommentPolicy(),
        publication_authorization=(
            publication_authorization or AllowPublicationAuthorization()
        ),
        publication_governance=AllowPublicationGovernance(),
        asset_reference_validation=AllowAssetReferenceValidation(),
        asset_current_governance=AllowAssetCurrentGovernance(),
    )
    if enable_mutations:
        install_mutation_activation_interlock(app, _EnabledMutationGate())
    if with_health:
        app.state.release_identity = ReleaseIdentity(
            application_version="0.1.0",
            git_sha="a" * 40,
            build_id="b",
            artifact_digest="sha256:" + ("c" * 64),
        )
        app.state.deployment_environment = DeploymentEnvironment.PRODUCTION
        app.state.readiness_probe = _ReadyProbe()
        register_operational_health_routes(app)
    return app, factory


class TestTenantAuthorityHttp:
    def test_valid_jwt_active_membership_reaches_application(
        self, rsa_material, bootstrap_engine, runtime_engine
    ) -> None:
        private_key, public_jwk = rsa_material
        principal = uuid.uuid7()
        tenant = uuid.uuid7()
        seed_active_authority(
            bootstrap_engine, tenant_id=tenant, principal_id=principal
        )
        authority = KernelCurrentTenantAccessAuthority(_kernel(runtime_engine))
        factory = RecordingUowFactory()
        app, factory = _app(
            authenticator=_authenticator(public_jwk),
            authority=authority,
            uow_factory=factory,
            enable_mutations=True,
        )
        client = TestClient(app, raise_server_exceptions=False)
        token = _mint(private_key, principal_id=principal)
        listed = client.get("/api/v1/contents", headers=_headers(tenant, token))
        assert factory.calls == 1
        assert listed.status_code == 500

    @pytest.mark.parametrize(
        "setup",
        [
            "missing_membership",
            "revoked_membership",
            "suspended_principal",
            "disabled_principal",
            "suspended_tenant",
            "disabled_tenant",
        ],
    )
    def test_tenant_denial_zero_uow(
        self, rsa_material, bootstrap_engine, runtime_engine, setup: str
    ) -> None:
        private_key, public_jwk = rsa_material
        principal = uuid.uuid7()
        tenant = uuid.uuid7()
        if setup == "missing_membership":
            seed_principal(bootstrap_engine, principal)
            seed_tenant(bootstrap_engine, tenant)
        elif setup == "revoked_membership":
            seed_active_authority(
                bootstrap_engine, tenant_id=tenant, principal_id=principal
            )
            revoke_membership(
                bootstrap_engine, tenant_id=tenant, principal_id=principal
            )
        elif setup == "suspended_principal":
            seed_principal(
                bootstrap_engine, principal, status=PrincipalStatus.SUSPENDED
            )
            seed_tenant(bootstrap_engine, tenant)
            from tests.platform.security.authorization.helpers import seed_membership

            seed_membership(
                bootstrap_engine, tenant_id=tenant, principal_id=principal
            )
        elif setup == "disabled_principal":
            seed_principal(
                bootstrap_engine, principal, status=PrincipalStatus.DISABLED
            )
            seed_tenant(bootstrap_engine, tenant)
            from tests.platform.security.authorization.helpers import seed_membership

            seed_membership(
                bootstrap_engine, tenant_id=tenant, principal_id=principal
            )
        elif setup == "suspended_tenant":
            seed_principal(bootstrap_engine, principal)
            seed_tenant(bootstrap_engine, tenant, status=TenantStatus.SUSPENDED)
            from tests.platform.security.authorization.helpers import seed_membership

            seed_membership(
                bootstrap_engine, tenant_id=tenant, principal_id=principal
            )
        else:
            seed_principal(bootstrap_engine, principal)
            seed_tenant(bootstrap_engine, tenant, status=TenantStatus.DISABLED)
            from tests.platform.security.authorization.helpers import seed_membership

            seed_membership(
                bootstrap_engine, tenant_id=tenant, principal_id=principal
            )
        authority = KernelCurrentTenantAccessAuthority(_kernel(runtime_engine))
        factory = RecordingUowFactory()
        app, factory = _app(
            authenticator=_authenticator(public_jwk),
            authority=authority,
            uow_factory=factory,
            enable_mutations=True,
        )
        client = TestClient(app, raise_server_exceptions=False)
        token = _mint(private_key, principal_id=principal)
        response = client.get("/api/v1/contents", headers=_headers(tenant, token))
        _assert_problem(response, status=403, code="forbidden")
        assert factory.calls == 0

    def test_authority_unavailable_zero_uow(self, rsa_material) -> None:
        private_key, public_jwk = rsa_material
        principal = uuid.uuid7()
        tenant = uuid.uuid7()
        bad = create_engine(
            "postgresql+psycopg://nobody:bad@127.0.0.1:1/none",
            connect_args={"connect_timeout": 1},
        )
        authority = KernelCurrentTenantAccessAuthority(_kernel(bad))
        factory = RecordingUowFactory()
        app, factory = _app(
            authenticator=_authenticator(public_jwk),
            authority=authority,
            uow_factory=factory,
            enable_mutations=True,
        )
        client = TestClient(app, raise_server_exceptions=False)
        token = _mint(private_key, principal_id=principal)
        response = client.get("/api/v1/contents", headers=_headers(tenant, token))
        _assert_problem(response, status=503, code="authorization_unavailable")
        assert factory.calls == 0


class TestCorruptAuthorityHttp:
    def test_corrupt_tenant_authority_503_zero_uow(self, rsa_material) -> None:
        from datetime import UTC, datetime

        from aieos.platform.security.authorization.repository import (
            MembershipAuthorityRow,
            PrincipalAuthorityRow,
            TenantAccessBundle,
            TenantAuthorityRow,
        )

        private_key, public_jwk = rsa_material
        principal = uuid.uuid7()
        tenant = uuid.uuid7()
        now = datetime.now(UTC)

        class _CorruptTenantRepo:
            def load_tenant_access_bundle(self, *, principal_id, tenant_id):
                return TenantAccessBundle(
                    principal=PrincipalAuthorityRow(
                        principal_id=principal_id, status="CORRUPT"  # type: ignore[arg-type]
                    ),
                    tenant=TenantAuthorityRow(
                        tenant_id=tenant_id, status=TenantStatus.ACTIVE
                    ),
                    membership=MembershipAuthorityRow(
                        tenant_id=tenant_id,
                        principal_id=principal_id,
                        status=MembershipStatus.ACTIVE,
                        expires_at=None,
                        revoked_at=None,
                    ),
                    evaluated_at=now,
                )

            def load_capability_bundle(self, *, principal_id, tenant_id, capability):
                raise AssertionError("capability must not be consulted")

        kernel = AuthorizationKernel(
            create_engine("postgresql+psycopg://unused/unused"),
            known_capabilities=AIEOS_CONTENT_CAPABILITIES,
            repository=_CorruptTenantRepo(),  # type: ignore[arg-type]
        )
        factory = RecordingUowFactory()
        app, factory = _app(
            authenticator=_authenticator(public_jwk),
            authority=KernelCurrentTenantAccessAuthority(kernel),
            uow_factory=factory,
            enable_mutations=True,
        )
        client = TestClient(app, raise_server_exceptions=False)
        token = _mint(private_key, principal_id=principal)
        response = client.get("/api/v1/contents", headers=_headers(tenant, token))
        body = _assert_problem(
            response, status=503, code="authorization_unavailable"
        )
        blob = json.dumps(body).lower()
        assert "corrupt" not in blob
        assert "bogus" not in blob
        assert factory.calls == 0

    def test_corrupt_capability_authority_503_zero_uow(
        self, rsa_material, bootstrap_engine, runtime_engine
    ) -> None:
        from datetime import UTC, datetime

        from aieos.platform.security.authorization.decisions import (
            MembershipStatus as _MS,
            PrincipalStatus as _PS,
        )
        from aieos.platform.security.authorization.repository import (
            CapabilityBundle,
            GrantAuthorityRow,
            MembershipAuthorityRow,
            PrincipalAuthorityRow,
            TenantAuthorityRow,
        )

        private_key, public_jwk = rsa_material
        principal = uuid.uuid7()
        tenant = uuid.uuid7()
        seed_active_authority(
            bootstrap_engine, tenant_id=tenant, principal_id=principal
        )
        now = datetime.now(UTC)
        good_tenant = KernelCurrentTenantAccessAuthority(_kernel(runtime_engine))

        class _CorruptGrantRepo:
            def load_tenant_access_bundle(self, *, principal_id, tenant_id):
                raise AssertionError("tenant path uses runtime kernel")

            def load_capability_bundle(self, *, principal_id, tenant_id, capability):
                return CapabilityBundle(
                    principal=PrincipalAuthorityRow(
                        principal_id=principal_id, status=_PS.ACTIVE
                    ),
                    tenant=TenantAuthorityRow(
                        tenant_id=tenant_id, status=TenantStatus.ACTIVE
                    ),
                    membership=MembershipAuthorityRow(
                        tenant_id=tenant_id,
                        principal_id=principal_id,
                        status=_MS.ACTIVE,
                        expires_at=None,
                        revoked_at=None,
                    ),
                    grant=GrantAuthorityRow(
                        tenant_id=tenant_id,
                        principal_id=principal_id,
                        capability=capability,
                        status="CORRUPT",  # type: ignore[arg-type]
                        expires_at=None,
                        revoked_at=None,
                    ),
                    evaluated_at=now,
                )

        cap_kernel = AuthorizationKernel(
            create_engine("postgresql+psycopg://unused/unused"),
            known_capabilities=AIEOS_CONTENT_CAPABILITIES,
            repository=_CorruptGrantRepo(),  # type: ignore[arg-type]
        )
        factory = RecordingUowFactory()
        app, factory = _app(
            authenticator=_authenticator(public_jwk),
            authority=good_tenant,
            review_authorization=KernelReviewAuthorization(cap_kernel),
            uow_factory=factory,
            enable_mutations=True,
        )
        client = TestClient(app, raise_server_exceptions=False)
        token = _mint(private_key, principal_id=principal)
        headers = _headers(tenant, token)
        headers["If-Match"] = '"r0"'
        response = client.post(
            f"/api/v1/contents/{uuid.uuid7()}/versions/{uuid.uuid7()}/"
            "actions/submit-for-review",
            headers=headers,
        )
        body = _assert_problem(
            response, status=503, code="authorization_unavailable"
        )
        assert "corrupt" not in json.dumps(body).lower()
        assert factory.calls == 0


class TestCapabilityHttp:
    def test_missing_review_capability_zero_uow(
        self, rsa_material, bootstrap_engine, runtime_engine
    ) -> None:
        private_key, public_jwk = rsa_material
        principal = uuid.uuid7()
        tenant = uuid.uuid7()
        seed_active_authority(
            bootstrap_engine, tenant_id=tenant, principal_id=principal
        )
        kernel = _kernel(runtime_engine)
        factory = RecordingUowFactory()
        app, factory = _app(
            authenticator=_authenticator(public_jwk),
            authority=KernelCurrentTenantAccessAuthority(kernel),
            review_authorization=KernelReviewAuthorization(kernel),
            uow_factory=factory,
            enable_mutations=True,
        )
        client = TestClient(app, raise_server_exceptions=False)
        token = _mint(private_key, principal_id=principal)
        headers = _headers(tenant, token)
        headers["If-Match"] = '"r0"'
        response = client.post(
            f"/api/v1/contents/{uuid.uuid7()}/versions/{uuid.uuid7()}/"
            "actions/submit-for-review",
            headers=headers,
        )
        _assert_problem(response, status=403, code="forbidden")
        assert factory.calls == 0

    def test_missing_publish_capability_zero_uow(
        self, rsa_material, bootstrap_engine, runtime_engine
    ) -> None:
        private_key, public_jwk = rsa_material
        principal = uuid.uuid7()
        tenant = uuid.uuid7()
        seed_active_authority(
            bootstrap_engine, tenant_id=tenant, principal_id=principal
        )
        kernel = _kernel(runtime_engine)
        factory = RecordingUowFactory()
        app, factory = _app(
            authenticator=_authenticator(public_jwk),
            authority=KernelCurrentTenantAccessAuthority(kernel),
            publication_authorization=KernelPublicationAuthorization(kernel),
            uow_factory=factory,
            enable_mutations=True,
        )
        client = TestClient(app, raise_server_exceptions=False)
        token = _mint(private_key, principal_id=principal)
        headers = _headers(tenant, token)
        headers["If-Match"] = '"r0"'
        response = client.post(
            f"/api/v1/contents/{uuid.uuid7()}/actions/publish",
            json={"version_id": str(uuid.uuid7())},
            headers=headers,
        )
        _assert_problem(response, status=403, code="forbidden")
        assert factory.calls == 0

    def test_capability_authority_unavailable_zero_uow(
        self, rsa_material, bootstrap_engine, runtime_engine
    ) -> None:
        private_key, public_jwk = rsa_material
        principal = uuid.uuid7()
        tenant = uuid.uuid7()
        seed_active_authority(
            bootstrap_engine, tenant_id=tenant, principal_id=principal
        )
        good_authority = KernelCurrentTenantAccessAuthority(
            _kernel(runtime_engine)
        )
        bad = create_engine(
            "postgresql+psycopg://nobody:bad@127.0.0.1:1/none",
            connect_args={"connect_timeout": 1},
        )
        factory = RecordingUowFactory()
        app, factory = _app(
            authenticator=_authenticator(public_jwk),
            authority=good_authority,
            review_authorization=KernelReviewAuthorization(_kernel(bad)),
            uow_factory=factory,
            enable_mutations=True,
        )
        client = TestClient(app, raise_server_exceptions=False)
        token = _mint(private_key, principal_id=principal)
        headers = _headers(tenant, token)
        headers["If-Match"] = '"r0"'
        response = client.post(
            f"/api/v1/contents/{uuid.uuid7()}/versions/{uuid.uuid7()}/"
            "actions/submit-for-review",
            headers=headers,
        )
        _assert_problem(response, status=503, code="authorization_unavailable")
        assert factory.calls == 0

    def test_exact_valid_capability_reaches_application(
        self, rsa_material, bootstrap_engine, runtime_engine
    ) -> None:
        private_key, public_jwk = rsa_material
        principal = uuid.uuid7()
        tenant = uuid.uuid7()
        seed_active_authority(
            bootstrap_engine,
            tenant_id=tenant,
            principal_id=principal,
            capabilities=(CONTENT_REVIEW_SUBMIT,),
        )
        kernel = _kernel(runtime_engine)
        factory = RecordingUowFactory()
        app, factory = _app(
            authenticator=_authenticator(public_jwk),
            authority=KernelCurrentTenantAccessAuthority(kernel),
            review_authorization=KernelReviewAuthorization(kernel),
            uow_factory=factory,
            enable_mutations=True,
        )
        client = TestClient(app, raise_server_exceptions=False)
        token = _mint(private_key, principal_id=principal)
        headers = _headers(tenant, token)
        headers["If-Match"] = '"r0"'
        response = client.post(
            f"/api/v1/contents/{uuid.uuid7()}/versions/{uuid.uuid7()}/"
            "actions/submit-for-review",
            headers=headers,
        )
        assert factory.calls == 1
        assert response.status_code == 500

    def test_revocation_takes_effect_on_next_http_request(
        self, rsa_material, bootstrap_engine, runtime_engine
    ) -> None:
        private_key, public_jwk = rsa_material
        principal = uuid.uuid7()
        tenant = uuid.uuid7()
        seed_active_authority(
            bootstrap_engine,
            tenant_id=tenant,
            principal_id=principal,
            capabilities=(CONTENT_PUBLISH,),
        )
        kernel = _kernel(runtime_engine)
        factory = RecordingUowFactory()
        app, factory = _app(
            authenticator=_authenticator(public_jwk),
            authority=KernelCurrentTenantAccessAuthority(kernel),
            publication_authorization=KernelPublicationAuthorization(kernel),
            uow_factory=factory,
            enable_mutations=True,
        )
        client = TestClient(app, raise_server_exceptions=False)
        token = _mint(private_key, principal_id=principal)
        headers = _headers(tenant, token)
        headers["If-Match"] = '"r0"'
        first = client.post(
            f"/api/v1/contents/{uuid.uuid7()}/actions/publish",
            json={"version_id": str(uuid.uuid7())},
            headers=headers,
        )
        assert factory.calls == 1
        assert first.status_code == 500
        revoke_grant(
            bootstrap_engine,
            tenant_id=tenant,
            principal_id=principal,
            capability=CONTENT_PUBLISH,
        )
        headers["Idempotency-Key"] = f"test-{uuid.uuid7()}"
        second = client.post(
            f"/api/v1/contents/{uuid.uuid7()}/actions/publish",
            json={"version_id": str(uuid.uuid7())},
            headers=headers,
        )
        _assert_problem(second, status=403, code="forbidden")
        assert factory.calls == 1


class TestSpoofingAndJwtClaimsIgnored:
    def test_spoof_headers_do_not_authorize(
        self, rsa_material, bootstrap_engine, runtime_engine
    ) -> None:
        private_key, public_jwk = rsa_material
        principal = uuid.uuid7()
        other = uuid.uuid7()
        tenant = uuid.uuid7()
        seed_principal(bootstrap_engine, principal)
        seed_tenant(bootstrap_engine, tenant)
        authority = KernelCurrentTenantAccessAuthority(_kernel(runtime_engine))
        factory = RecordingUowFactory()
        app, factory = _app(
            authenticator=_authenticator(public_jwk),
            authority=authority,
            uow_factory=factory,
            enable_mutations=True,
        )
        client = TestClient(app, raise_server_exceptions=False)
        token = _mint(private_key, principal_id=principal)
        response = client.get(
            "/api/v1/contents",
            headers=_headers(
                tenant,
                token,
                **{
                    "X-AIEOS-Principal-ID": str(other),
                    "X-User-ID": str(other),
                    "X-Admin": "true",
                    "X-Role": "admin",
                    "X-Roles": "admin",
                    "X-Permissions": "content.publish",
                    "X-Capabilities": "content.publish",
                },
            ),
        )
        _assert_problem(response, status=403, code="forbidden")
        assert factory.calls == 0

    def test_jwt_roles_permissions_scope_ignored_for_authorization(
        self, rsa_material, bootstrap_engine, runtime_engine
    ) -> None:
        private_key, public_jwk = rsa_material
        principal = uuid.uuid7()
        tenant = uuid.uuid7()
        seed_principal(bootstrap_engine, principal)
        seed_tenant(bootstrap_engine, tenant)
        authority = KernelCurrentTenantAccessAuthority(_kernel(runtime_engine))
        factory = RecordingUowFactory()
        app, factory = _app(
            authenticator=_authenticator(public_jwk),
            authority=authority,
            uow_factory=factory,
            enable_mutations=True,
        )
        client = TestClient(app, raise_server_exceptions=False)
        token = _mint(
            private_key,
            principal_id=principal,
            extra_claims={
                "roles": ["admin"],
                "groups": ["ops"],
                "permissions": ["*"],
                "scope": "content.publish content.review.decide",
                "tenant_id": str(tenant),
            },
        )
        response = client.get("/api/v1/contents", headers=_headers(tenant, token))
        _assert_problem(response, status=403, code="forbidden")
        assert factory.calls == 0


class TestHealthIndependence:
    def test_health_without_auth_or_membership(self, rsa_material) -> None:
        private_key, public_jwk = rsa_material
        # Authority that would deny everything if consulted.
        authority = MutableCurrentTenantAccessAuthority()
        app, _factory = _app(
            authenticator=_authenticator(public_jwk),
            authority=authority,
            with_health=True,
        )
        client = TestClient(app, raise_server_exceptions=False)
        live = client.get("/livez")
        ready = client.get("/readyz")
        assert live.status_code == 200
        assert ready.status_code == 200
        assert authority.calls == []
        _ = private_key  # silence unused

"""PED-I08 JWT Bearer production authenticator adversarial HTTP tests."""

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
from jwt.exceptions import PyJWKClientConnectionError, PyJWKClientError

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
from aieos.platform.security.jwt_bearer import JwtBearerRequestIdentityAuthenticator
from tests.fakes import (
    AllowAssetCurrentGovernance,
    AllowAssetReferenceValidation,
    AllowPublicationAuthorization,
    AllowPublicationGovernance,
    AllowReviewAuthorization,
    AllowReviewCommentPolicy,
    IDEMPOTENCY_RETENTION,
    MutableCurrentTenantAccessAuthority,
    RecordingUowFactory,
    make_test_schema_registry,
)

pytestmark = pytest.mark.ped_i08

ISSUER = "https://issuer.example.test/"
AUDIENCE = "aieos-api"
JWKS_URI = "https://issuer.example.test/.well-known/jwks.json"
KID = "ped-i08-test-key"

CREATE_BODY = {
    "content_type": "test.generic",
    "title": "Title",
    "description": "Description",
    "locale": "en-IN",
}
SECRET_SENTINEL = "SECRET_JWKS_PROVIDER_TOKEN_xyzzy"
LEAK_NEEDLES = (
    SECRET_SENTINEL,
    "Traceback",
    "sqlalchemy",
    "password",
    "Bearer ",
    "BEGIN RSA",
    ISSUER,
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


class _UnavailableJwkClient:
    def get_signing_key_from_jwt(self, token: str) -> PyJWK:
        raise PyJWKClientConnectionError(SECRET_SENTINEL)


class _BrokenJwksDocumentClient:
    def get_signing_key_from_jwt(self, token: str) -> PyJWK:
        raise PyJWKClientError(SECRET_SENTINEL)


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
    issuer: str = ISSUER,
    audience: str = AUDIENCE,
    alg: str = "RS256",
    headers: dict[str, Any] | None = None,
    omit_header_keys: set[str] | None = None,
    extra_claims: dict[str, Any] | None = None,
    omit: set[str] | None = None,
    exp_delta: int = 3600,
    nbf_delta: int | None = None,
    iat_delta: int = 0,
) -> str:
    claims: dict[str, Any] = {
        "iss": issuer,
        "aud": audience,
        "sub": "idp-subject-1",
        "client_id": "aieos-client",
        "jti": str(uuid.uuid4()),
        "iat": _now() + iat_delta,
        "exp": _now() + exp_delta,
        AIEOS_PRINCIPAL_ID_CLAIM: str(principal_id),
    }
    if nbf_delta is not None:
        claims["nbf"] = _now() + nbf_delta
    if extra_claims:
        claims.update(extra_claims)
    if omit:
        for key in omit:
            claims.pop(key, None)
    hdr = {"kid": KID, "typ": "at+jwt"}
    if headers:
        hdr.update(headers)
    if omit_header_keys:
        for key in omit_header_keys:
            hdr.pop(key, None)
    return jwt.encode(claims, private_key, algorithm=alg, headers=hdr)


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
    with_health: bool = False,
):
    factory = uow_factory or RecordingUowFactory()
    app = create_app(
        uow_factory=factory,
        teaching_uow_factory=factory,
        request_identity_authenticator=authenticator,
        security_resolver=CurrentAuthoritySecurityContextResolver(authority),
        content_types=StaticContentTypeCatalog({"test.generic"}),
        cursor_signing_key=b"ped-i08-test-cursor-signing-key",
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


def _authenticator(public_jwk: dict[str, Any], *, jwk_client=None):
    config = AuthRuntimeConfig(issuer=ISSUER, audience=AUDIENCE, jwks_uri=JWKS_URI)
    return JwtBearerRequestIdentityAuthenticator(
        config,
        jwk_client=jwk_client or _StaticJwkClient(public_jwk),
    )


class TestValidAuthentication:
    def test_valid_token_principal_and_tenant_authority(self, rsa_material) -> None:
        private_key, public_jwk = rsa_material
        principal = uuid.uuid7()
        tenant = uuid.uuid7()
        authority = MutableCurrentTenantAccessAuthority()
        authority.grant(principal, tenant)
        factory = RecordingUowFactory()
        token = _mint(private_key, principal_id=principal)
        app, factory = _app(
            authenticator=_authenticator(public_jwk),
            authority=authority,
            uow_factory=factory,
            enable_mutations=True,
        )
        client = TestClient(app, raise_server_exceptions=False)
        listed = client.get("/api/v1/contents", headers=_headers(tenant, token))
        assert factory.calls == 1
        assert authority.calls == [(principal, tenant)]
        assert listed.status_code == 500
        created = client.post(
            "/api/v1/contents",
            json=CREATE_BODY,
            headers=_headers(tenant, token),
        )
        assert factory.calls == 2
        assert created.status_code == 500


class TestCredentialFailures:
    @pytest.mark.parametrize(
        "authorization",
        [
            None,
            "",
            "Basic abc",
            "Bearer",
            "Bearer ",
            "bearer token",
            "Bearer a.b",
            "Bearer a.b.c.d.e",
        ],
    )
    def test_missing_or_malformed_bearer_401_zero_uow(
        self, rsa_material, authorization: str | None
    ) -> None:
        _, public_jwk = rsa_material
        principal = uuid.uuid7()
        tenant = uuid.uuid7()
        authority = MutableCurrentTenantAccessAuthority()
        authority.grant(principal, tenant)
        factory = RecordingUowFactory()
        app, factory = _app(
            authenticator=_authenticator(public_jwk),
            authority=authority,
            uow_factory=factory,
            enable_mutations=True,
        )
        client = TestClient(app, raise_server_exceptions=False)
        headers = _headers(tenant)
        if authorization is None:
            headers.pop("Authorization", None)
        else:
            headers["Authorization"] = authorization
        read = client.get("/api/v1/contents", headers=headers)
        _assert_problem(read, status=401, code="unauthenticated")
        assert factory.calls == 0
        mutate = client.post(
            "/api/v1/contents", json=CREATE_BODY, headers=headers
        )
        _assert_problem(mutate, status=401, code="unauthenticated")
        assert factory.calls == 0

    def test_invalid_signature_401_zero_uow(self, rsa_material) -> None:
        private_key, public_jwk = rsa_material
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        principal = uuid.uuid7()
        tenant = uuid.uuid7()
        authority = MutableCurrentTenantAccessAuthority()
        authority.grant(principal, tenant)
        factory = RecordingUowFactory()
        token = _mint(other, principal_id=principal)
        app, factory = _app(
            authenticator=_authenticator(public_jwk),
            authority=authority,
            uow_factory=factory,
            enable_mutations=True,
        )
        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(
            "/api/v1/contents",
            json=CREATE_BODY,
            headers=_headers(tenant, token),
        )
        _assert_problem(response, status=401, code="unauthenticated")
        assert factory.calls == 0

    @pytest.mark.parametrize("alg", ["none", "HS256", "ES256"])
    def test_non_rs256_rejected(self, rsa_material, alg: str) -> None:
        private_key, public_jwk = rsa_material
        principal = uuid.uuid7()
        tenant = uuid.uuid7()
        authority = MutableCurrentTenantAccessAuthority()
        authority.grant(principal, tenant)
        factory = RecordingUowFactory()
        if alg == "none":
            claims = {
                "iss": ISSUER,
                "aud": AUDIENCE,
                "sub": "s",
                "client_id": "c",
                "jti": str(uuid.uuid4()),
                "iat": _now(),
                "exp": _now() + 3600,
                AIEOS_PRINCIPAL_ID_CLAIM: str(principal),
            }
            token = jwt.encode(claims, key=None, algorithm="none", headers={"kid": KID})
        elif alg == "HS256":
            token = jwt.encode(
                {
                    "iss": ISSUER,
                    "aud": AUDIENCE,
                    "sub": "s",
                    "client_id": "c",
                    "jti": str(uuid.uuid4()),
                    "iat": _now(),
                    "exp": _now() + 3600,
                    AIEOS_PRINCIPAL_ID_CLAIM: str(principal),
                },
                key="not-a-secret-for-prod",
                algorithm="HS256",
                headers={"kid": KID},
            )
        else:
            # ES256 requires EC key; minting with RSA private fails — craft header only path
            # by encoding with a disposable EC key.
            from cryptography.hazmat.primitives.asymmetric import ec

            ec_key = ec.generate_private_key(ec.SECP256R1())
            token = jwt.encode(
                {
                    "iss": ISSUER,
                    "aud": AUDIENCE,
                    "sub": "s",
                    "client_id": "c",
                    "jti": str(uuid.uuid4()),
                    "iat": _now(),
                    "exp": _now() + 3600,
                    AIEOS_PRINCIPAL_ID_CLAIM: str(principal),
                },
                key=ec_key,
                algorithm="ES256",
                headers={"kid": KID},
            )
        app, factory = _app(
            authenticator=_authenticator(public_jwk),
            authority=authority,
            uow_factory=factory,
        )
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/v1/contents", headers=_headers(tenant, token))
        _assert_problem(response, status=401, code="unauthenticated")
        assert factory.calls == 0

    def test_wrong_issuer(self, rsa_material) -> None:
        private_key, public_jwk = rsa_material
        principal = uuid.uuid7()
        tenant = uuid.uuid7()
        authority = MutableCurrentTenantAccessAuthority()
        authority.grant(principal, tenant)
        factory = RecordingUowFactory()
        token = _mint(
            private_key,
            principal_id=principal,
            issuer="https://evil.example.test/",
        )
        app, factory = _app(
            authenticator=_authenticator(public_jwk),
            authority=authority,
            uow_factory=factory,
        )
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/v1/contents", headers=_headers(tenant, token))
        _assert_problem(response, status=401, code="unauthenticated")
        assert factory.calls == 0

    def test_wrong_audience(self, rsa_material) -> None:
        private_key, public_jwk = rsa_material
        principal = uuid.uuid7()
        tenant = uuid.uuid7()
        authority = MutableCurrentTenantAccessAuthority()
        authority.grant(principal, tenant)
        factory = RecordingUowFactory()
        token = _mint(private_key, principal_id=principal, audience="other-api")
        app, factory = _app(
            authenticator=_authenticator(public_jwk),
            authority=authority,
            uow_factory=factory,
        )
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/v1/contents", headers=_headers(tenant, token))
        _assert_problem(response, status=401, code="unauthenticated")
        assert factory.calls == 0

    def test_expired_token(self, rsa_material) -> None:
        private_key, public_jwk = rsa_material
        principal = uuid.uuid7()
        tenant = uuid.uuid7()
        authority = MutableCurrentTenantAccessAuthority()
        authority.grant(principal, tenant)
        factory = RecordingUowFactory()
        token = _mint(private_key, principal_id=principal, exp_delta=-60, iat_delta=-120)
        app, factory = _app(
            authenticator=_authenticator(public_jwk),
            authority=authority,
            uow_factory=factory,
        )
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/v1/contents", headers=_headers(tenant, token))
        _assert_problem(response, status=401, code="unauthenticated")
        assert factory.calls == 0

    def test_future_nbf(self, rsa_material) -> None:
        private_key, public_jwk = rsa_material
        principal = uuid.uuid7()
        tenant = uuid.uuid7()
        authority = MutableCurrentTenantAccessAuthority()
        authority.grant(principal, tenant)
        factory = RecordingUowFactory()
        token = _mint(private_key, principal_id=principal, nbf_delta=3600)
        app, factory = _app(
            authenticator=_authenticator(public_jwk),
            authority=authority,
            uow_factory=factory,
        )
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/v1/contents", headers=_headers(tenant, token))
        _assert_problem(response, status=401, code="unauthenticated")
        assert factory.calls == 0

    def test_missing_principal_claim(self, rsa_material) -> None:
        private_key, public_jwk = rsa_material
        principal = uuid.uuid7()
        tenant = uuid.uuid7()
        authority = MutableCurrentTenantAccessAuthority()
        authority.grant(principal, tenant)
        factory = RecordingUowFactory()
        token = _mint(
            private_key, principal_id=principal, omit={AIEOS_PRINCIPAL_ID_CLAIM}
        )
        app, factory = _app(
            authenticator=_authenticator(public_jwk),
            authority=authority,
            uow_factory=factory,
        )
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/v1/contents", headers=_headers(tenant, token))
        _assert_problem(response, status=401, code="unauthenticated")
        assert factory.calls == 0

    def test_non_uuid_principal_claim(self, rsa_material) -> None:
        private_key, public_jwk = rsa_material
        principal = uuid.uuid7()
        tenant = uuid.uuid7()
        authority = MutableCurrentTenantAccessAuthority()
        authority.grant(principal, tenant)
        factory = RecordingUowFactory()
        token = _mint(
            private_key,
            principal_id=principal,
            extra_claims={AIEOS_PRINCIPAL_ID_CLAIM: "not-a-uuid"},
        )
        app, factory = _app(
            authenticator=_authenticator(public_jwk),
            authority=authority,
            uow_factory=factory,
        )
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/v1/contents", headers=_headers(tenant, token))
        _assert_problem(response, status=401, code="unauthenticated")
        assert factory.calls == 0

    @pytest.mark.parametrize(
        "typ_case",
        [
            ("absent", None),
            ("JWT", "JWT"),
            ("ID", "ID"),
        ],
    )
    def test_typ_must_be_exactly_at_jwt_or_401_zero_uow(
        self, rsa_material, typ_case: tuple[str, str | None]
    ) -> None:
        _label, typ_value = typ_case
        private_key, public_jwk = rsa_material
        principal = uuid.uuid7()
        tenant = uuid.uuid7()
        authority = MutableCurrentTenantAccessAuthority()
        authority.grant(principal, tenant)
        factory = RecordingUowFactory()
        if typ_value is None:
            token = _mint(
                private_key, principal_id=principal, omit_header_keys={"typ"}
            )
        else:
            token = _mint(
                private_key, principal_id=principal, headers={"typ": typ_value}
            )
        app, factory = _app(
            authenticator=_authenticator(public_jwk),
            authority=authority,
            uow_factory=factory,
            enable_mutations=True,
        )
        client = TestClient(app, raise_server_exceptions=False)
        read = client.get("/api/v1/contents", headers=_headers(tenant, token))
        _assert_problem(read, status=401, code="unauthenticated")
        assert factory.calls == 0
        mutate = client.post(
            "/api/v1/contents", json=CREATE_BODY, headers=_headers(tenant, token)
        )
        _assert_problem(mutate, status=401, code="unauthenticated")
        assert factory.calls == 0
        assert authority.calls == []

    def test_typ_at_jwt_authenticates_principal(self, rsa_material) -> None:
        private_key, public_jwk = rsa_material
        principal = uuid.uuid7()
        tenant = uuid.uuid7()
        authority = MutableCurrentTenantAccessAuthority()
        authority.grant(principal, tenant)
        factory = RecordingUowFactory()
        token = _mint(
            private_key, principal_id=principal, headers={"typ": "at+jwt"}
        )
        app, factory = _app(
            authenticator=_authenticator(public_jwk),
            authority=authority,
            uow_factory=factory,
        )
        client = TestClient(app, raise_server_exceptions=False)
        listed = client.get("/api/v1/contents", headers=_headers(tenant, token))
        assert factory.calls == 1
        assert authority.calls == [(principal, tenant)]
        assert listed.status_code == 500


class TestSpoofingAndTenantSeparation:
    def test_spoofed_claims_do_not_grant_authority(self, rsa_material) -> None:
        private_key, public_jwk = rsa_material
        principal = uuid.uuid7()
        tenant_ok = uuid.uuid7()
        tenant_claim = uuid.uuid7()
        authority = MutableCurrentTenantAccessAuthority()
        authority.grant(principal, tenant_ok)
        factory = RecordingUowFactory()
        token = _mint(
            private_key,
            principal_id=principal,
            extra_claims={
                "roles": ["admin"],
                "groups": ["superusers"],
                "scope": "content.publish content.review.decide",
                "permissions": ["*"],
                "tenant_id": str(tenant_claim),
                "tenant_ids": [str(tenant_claim)],
                "is_admin": True,
            },
        )
        app, factory = _app(
            authenticator=_authenticator(public_jwk),
            authority=authority,
            uow_factory=factory,
            enable_mutations=True,
        )
        client = TestClient(app, raise_server_exceptions=False)
        denied = client.get(
            "/api/v1/contents", headers=_headers(tenant_claim, token)
        )
        _assert_problem(denied, status=403, code="forbidden")
        assert factory.calls == 0
        assert authority.calls == [(principal, tenant_claim)]

    def test_spoofed_principal_headers_ignored(self, rsa_material) -> None:
        private_key, public_jwk = rsa_material
        principal = uuid.uuid7()
        spoofed = uuid.uuid7()
        tenant = uuid.uuid7()
        authority = MutableCurrentTenantAccessAuthority()
        authority.grant(principal, tenant)
        factory = RecordingUowFactory()
        token = _mint(private_key, principal_id=principal)
        app, factory = _app(
            authenticator=_authenticator(public_jwk),
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
                token,
                **{
                    "X-AIEOS-Principal-ID": str(spoofed),
                    "X-Principal-ID": str(spoofed),
                    "X-User-ID": str(spoofed),
                    "X-Admin": "true",
                    "X-Role": "admin",
                    "X-Roles": "admin",
                    "X-Permissions": "*",
                    "X-Capabilities": "content.publish",
                },
            ),
        )
        assert factory.calls == 1
        assert authority.calls == [(principal, tenant)]
        assert (spoofed, tenant) not in authority.calls
        assert response.status_code == 500

    def test_unauthorized_tenant_403_zero_uow(self, rsa_material) -> None:
        private_key, public_jwk = rsa_material
        principal = uuid.uuid7()
        tenant_ok = uuid.uuid7()
        tenant_other = uuid.uuid7()
        authority = MutableCurrentTenantAccessAuthority()
        authority.grant(principal, tenant_ok)
        factory = RecordingUowFactory()
        token = _mint(private_key, principal_id=principal)
        app, factory = _app(
            authenticator=_authenticator(public_jwk),
            authority=authority,
            uow_factory=factory,
            enable_mutations=True,
        )
        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(
            "/api/v1/contents",
            json=CREATE_BODY,
            headers=_headers(tenant_other, token),
        )
        _assert_problem(response, status=403, code="forbidden")
        assert factory.calls == 0

    def test_revocation_between_requests(self, rsa_material) -> None:
        private_key, public_jwk = rsa_material
        principal = uuid.uuid7()
        tenant = uuid.uuid7()
        authority = MutableCurrentTenantAccessAuthority()
        authority.grant(principal, tenant)
        factory = RecordingUowFactory()
        token = _mint(private_key, principal_id=principal)
        app, factory = _app(
            authenticator=_authenticator(public_jwk),
            authority=authority,
            uow_factory=factory,
        )
        client = TestClient(app, raise_server_exceptions=False)
        first = client.get("/api/v1/contents", headers=_headers(tenant, token))
        assert factory.calls == 1
        assert first.status_code == 500
        authority.revoke(principal, tenant)
        second = client.get("/api/v1/contents", headers=_headers(tenant, token))
        _assert_problem(second, status=403, code="forbidden")
        assert factory.calls == 1

    def test_suspended_tenant_denied(self, rsa_material) -> None:
        private_key, public_jwk = rsa_material
        principal = uuid.uuid7()
        tenant = uuid.uuid7()
        authority = MutableCurrentTenantAccessAuthority()
        authority.grant(principal, tenant)
        authority.suspend(tenant)
        factory = RecordingUowFactory()
        token = _mint(private_key, principal_id=principal)
        app, factory = _app(
            authenticator=_authenticator(public_jwk),
            authority=authority,
            uow_factory=factory,
        )
        client = TestClient(app, raise_server_exceptions=False)
        denied = client.get("/api/v1/contents", headers=_headers(tenant, token))
        _assert_problem(denied, status=403, code="forbidden")
        assert factory.calls == 0


class TestUnavailableAndHealth:
    def test_jwks_unavailable_503_sanitized_zero_uow(self, rsa_material) -> None:
        private_key, _public_jwk = rsa_material
        principal = uuid.uuid7()
        tenant = uuid.uuid7()
        authority = MutableCurrentTenantAccessAuthority()
        authority.grant(principal, tenant)
        factory = RecordingUowFactory()
        token = _mint(private_key, principal_id=principal)
        app, factory = _app(
            authenticator=_authenticator({}, jwk_client=_UnavailableJwkClient()),
            authority=authority,
            uow_factory=factory,
            enable_mutations=True,
        )
        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(
            "/api/v1/contents",
            json=CREATE_BODY,
            headers=_headers(tenant, token),
        )
        _assert_problem(response, status=503, code="authentication_unavailable")
        assert factory.calls == 0
        broken = client.get("/api/v1/contents", headers=_headers(tenant, token))
        _assert_problem(broken, status=503, code="authentication_unavailable")
        assert factory.calls == 0

    def test_jwks_document_failure_503_sanitized(self, rsa_material) -> None:
        private_key, _public_jwk = rsa_material
        principal = uuid.uuid7()
        tenant = uuid.uuid7()
        authority = MutableCurrentTenantAccessAuthority()
        authority.grant(principal, tenant)
        factory = RecordingUowFactory()
        token = _mint(private_key, principal_id=principal)
        app, factory = _app(
            authenticator=_authenticator({}, jwk_client=_BrokenJwksDocumentClient()),
            authority=authority,
            uow_factory=factory,
        )
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/v1/contents", headers=_headers(tenant, token))
        _assert_problem(response, status=503, code="authentication_unavailable")
        assert factory.calls == 0

    def test_livez_readyz_independent_of_jwks(self, rsa_material) -> None:
        _private_key, _public_jwk = rsa_material
        principal = uuid.uuid7()
        authority = MutableCurrentTenantAccessAuthority()
        factory = RecordingUowFactory()
        app, factory = _app(
            authenticator=_authenticator({}, jwk_client=_UnavailableJwkClient()),
            authority=authority,
            uow_factory=factory,
            with_health=True,
        )
        client = TestClient(app, raise_server_exceptions=False)
        livez = client.get("/livez")
        readyz = client.get("/readyz")
        assert livez.status_code == 200
        assert readyz.status_code == 200
        assert factory.calls == 0

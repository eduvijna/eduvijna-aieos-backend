"""PED-I08 architecture boundaries for JWT Bearer production authentication."""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from aieos.platform.api.app import create_app
from aieos.platform.api.openapi import build_openapi, canonical_openapi_json
from aieos.platform.security.auth_config import (
    AIEOS_PRINCIPAL_ID_CLAIM,
    AuthConfigurationError,
    AuthRuntimeConfig,
    load_auth_runtime_config,
)
from aieos.platform.security.identity import TrustedRequestIdentity
from aieos.platform.security.jwt_bearer import JwtBearerRequestIdentityAuthenticator
from tests.dbutil import REPO_ROOT
from tests.fakes import (
    AllowAssetCurrentGovernance,
    AllowAssetReferenceValidation,
    AllowPublicationAuthorization,
    AllowPublicationGovernance,
    AllowReviewAuthorization,
    AllowReviewCommentPolicy,
    FixedPrincipalAuthenticator,
    StubSecurityContextResolver,
)
from datetime import timedelta
from aieos.domains.content.application.catalog import StaticContentTypeCatalog
from aieos.domains.content.domain.schema import ContentSchemaRegistry

pytestmark = pytest.mark.ped_i08

SRC_ROOT = REPO_ROOT / "src" / "aieos"
SECURITY_ROOT = SRC_ROOT / "platform" / "security"
API_ROOT = SRC_ROOT / "platform" / "api"
CONTENT_DOMAIN = SRC_ROOT / "domains" / "content" / "domain"
CONTENT_APP = SRC_ROOT / "domains" / "content" / "application"
JWT_MODULE = SECURITY_ROOT / "jwt_bearer.py"
BOUNDARY_DOC = REPO_ROOT / "docs" / "PED-I08-PRODUCTION-REQUEST-AUTHENTICATOR.md"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"
MIGRATIONS = REPO_ROOT / "migrations" / "versions"
PYPROJECT = REPO_ROOT / "pyproject.toml"
SNAPSHOT = REPO_ROOT / "contracts" / "openapi" / "aieos-v1.json"
COMMON = REPO_ROOT / "tools" / "release" / "common.py"

_FORBIDDEN_AUTH_LIBS = (
    "authlib",
    "python-jose",
    "openid",
    "oidc",
    "oauthlib",
    "msal",
    "auth0",
    "keycloak",
    "cerbos",
    "openfga",
    "casbin",
    "opa",
    "jose",
)

_JWT_AUTHORIZED_RELATIVE = Path("jwt_bearer.py")


def _py_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*.py") if p.is_file()]


class _UnusedUowFactory:
    def __call__(self, execution_tenant_id):
        raise AssertionError("OpenAPI export must not touch persistence")


def test_trusted_request_identity_remains_principal_only() -> None:
    fields = {f.name for f in TrustedRequestIdentity.__dataclass_fields__.values()}
    assert fields == {"principal_id"}
    identity = TrustedRequestIdentity(principal_id=uuid4())
    assert isinstance(identity.principal_id, UUID)
    assert not hasattr(identity, "token")
    assert not hasattr(identity, "claims")
    assert not hasattr(identity, "roles")
    assert not hasattr(identity, "scopes")
    assert not hasattr(identity, "tenant_id")


def test_jwt_authenticator_port_shape() -> None:
    assert hasattr(JwtBearerRequestIdentityAuthenticator, "authenticate")
    config = AuthRuntimeConfig(
        issuer="https://issuer.example.test/",
        audience="aieos-api",
        jwks_uri="https://issuer.example.test/.well-known/jwks.json",
    )
    auth = JwtBearerRequestIdentityAuthenticator(config)
    assert callable(auth.authenticate)


def test_auth_config_fail_closed_no_defaults() -> None:
    with pytest.raises(AuthConfigurationError):
        load_auth_runtime_config({})
    with pytest.raises(AuthConfigurationError):
        load_auth_runtime_config(
            {
                "AIEOS_AUTH_ISSUER": "https://issuer.example.test/",
                "AIEOS_AUTH_AUDIENCE": "aieos-api",
                "AIEOS_AUTH_JWKS_URI": "http://issuer.example.test/jwks",
            }
        )
    cfg = load_auth_runtime_config(
        {
            "AIEOS_AUTH_ISSUER": "https://issuer.example.test/",
            "AIEOS_AUTH_AUDIENCE": "aieos-api",
            "AIEOS_AUTH_JWKS_URI": "https://issuer.example.test/.well-known/jwks.json",
        }
    )
    assert cfg.issuer == "https://issuer.example.test/"
    assert cfg.audience == "aieos-api"
    assert AIEOS_PRINCIPAL_ID_CLAIM == "https://eduvijna.com/claims/aieos/principal_id"


def test_pyjwt_confined_to_authorized_module() -> None:
    hits: list[str] = []
    for path in _py_files(SRC_ROOT):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            for name in modules:
                root = name.split(".")[0].lower()
                if root in {"jwt", "pyjwt"}:
                    if path.name != _JWT_AUTHORIZED_RELATIVE.name:
                        hits.append(f"{path.relative_to(REPO_ROOT)}:{name}")
                for needle in _FORBIDDEN_AUTH_LIBS:
                    if needle.lower() == root:
                        hits.append(f"{path.relative_to(REPO_ROOT)}:{name}")
    assert hits == []
    deps = PYPROJECT.read_text(encoding="utf-8").lower()
    assert "pyjwt>=" in deps
    assert "cryptography" in deps
    for needle in ("authlib", "python-jose", "msal", "cerbos", "casbin", "openfga"):
        assert needle not in deps


def test_content_domain_does_not_import_auth_sdk_or_request() -> None:
    for root in (CONTENT_DOMAIN, CONTENT_APP):
        for path in _py_files(root):
            text = path.read_text(encoding="utf-8")
            assert "starlette.requests" not in text
            assert "fastapi.Request" not in text
            assert "import jwt" not in text
            assert "from jwt" not in text
            assert "JwtBearerRequestIdentityAuthenticator" not in text
            assert "PyJWKClient" not in text


def test_authenticator_does_not_authorize_tenants() -> None:
    text = JWT_MODULE.read_text(encoding="utf-8")
    assert "CurrentTenantAccessAuthority" not in text
    assert "authorize_tenant" not in text
    assert "ReviewAuthorization" not in text
    assert "PublicationAuthorization" not in text
    assert "X-AIEOS-Tenant-ID" not in text
    assert "X-AIEOS-Principal-ID" not in text


def test_no_principal_mapping_persistence_or_migration() -> None:
    versions = sorted(p.name for p in MIGRATIONS.glob("*.py") if p.name != "__init__.py")
    assert "pedi090001_security_authority.py" in versions
    assert "pedi10b2001_asset_authority_sor.py" in versions
    assert versions[-1] == "tosd070002_teaching_execution_audit.py"
    for path in MIGRATIONS.rglob("*.py"):
        body = path.read_text(encoding="utf-8")
        assert "pedi080001" not in body
        assert "principal_mapping" not in body.lower()
        assert "external_identity" not in body.lower()
    for path in _py_files(SECURITY_ROOT):
        text = path.read_text(encoding="utf-8")
        assert "create_all" not in text
        assert "from alembic" not in text
        assert "import alembic" not in text


def test_openapi_exposes_aieos_bearer_auth_scheme() -> None:
    app = create_app(
        uow_factory=_UnusedUowFactory(),
        teaching_uow_factory=_UnusedUowFactory(),
        request_identity_authenticator=FixedPrincipalAuthenticator(uuid4()),
        security_resolver=StubSecurityContextResolver(uuid4(), uuid4()),
        content_types=StaticContentTypeCatalog({"test.generic"}),
        cursor_signing_key=b"ped-i08-openapi-key",
        schema_registry=ContentSchemaRegistry(),
        idempotency_retention=timedelta(hours=24),
        review_authorization=AllowReviewAuthorization(),
        review_comment_policy=AllowReviewCommentPolicy(),
        publication_authorization=AllowPublicationAuthorization(),
        publication_governance=AllowPublicationGovernance(),
        asset_reference_validation=AllowAssetReferenceValidation(),
        asset_current_governance=AllowAssetCurrentGovernance(),
    )
    schema = build_openapi(app)
    schemes = schema["components"]["securitySchemes"]
    assert schemes["AIEOSBearerAuth"] == {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
        "description": (
            "ADR-AIEOS-030 JWT access token (RS256). Authentication of principal "
            "identity only; not tenant authority or capability authorization."
        ),
    }
    assert schema["security"] == [{"AIEOSBearerAuth": []}]
    openapi_src = (API_ROOT / "openapi.py").read_text(encoding="utf-8")
    assert "OAuth2PasswordBearer" not in openapi_src
    assert "authorizationCode" not in openapi_src
    assert "/livez" not in schema.get("paths", {})
    assert "/readyz" not in schema.get("paths", {})


def test_openapi_snapshot_matches_checked_in_and_hash() -> None:
    app = create_app(
        uow_factory=_UnusedUowFactory(),
        teaching_uow_factory=_UnusedUowFactory(),
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
    dumped = canonical_openapi_json(build_openapi(app))
    assert SNAPSHOT.read_text(encoding="utf-8") == dumped
    digest = hashlib.sha256(SNAPSHOT.read_bytes()).hexdigest().upper()
    common = COMMON.read_text(encoding="utf-8")
    assert digest in common
    assert "AIEOSBearerAuth" in dumped


def test_boundary_doc_and_changelog_and_marker() -> None:
    doc = BOUNDARY_DOC.read_text(encoding="utf-8")
    for needle in (
        "ADR-AIEOS-030",
        "Authorization: Bearer",
        "RS256",
        "AIEOS_AUTH_ISSUER",
        "AIEOS_AUTH_AUDIENCE",
        "AIEOS_AUTH_JWKS_URI",
        AIEOS_PRINCIPAL_ID_CLAIM,
        "authentication_unavailable",
        "NOT AUTHORIZED",
        "zero-UoW",
        "OpenAPI",
    ):
        assert needle in doc
    assert "production-ready" not in doc.lower()
    assert "safe to deploy" not in doc.lower()
    changelog = CHANGELOG.read_text(encoding="utf-8")
    assert "PED-I08" in changelog
    assert "production authentication complete" not in changelog.lower()
    assert "production-ready" not in changelog.lower()
    markers = PYPROJECT.read_text(encoding="utf-8")
    assert "ped_i08:" in markers

"""PED-I07 architecture boundaries for trusted request identity."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from aieos.platform.api.app import create_app
from aieos.platform.runtime.composition import ApiRuntimeDependencies
from aieos.platform.security.authenticator import RequestIdentityAuthenticator
from aieos.platform.security.authority import (
    CurrentAuthoritySecurityContextResolver,
    CurrentTenantAccessAuthority,
)
from aieos.platform.security.context import (
    AuthenticationUnavailableError,
    AuthorizationUnavailableError,
    SecurityContextResolver,
    TrustedSecurityContext,
    UnauthenticatedError,
    UnauthorizedError,
)
from aieos.platform.security.identity import TrustedRequestIdentity
from tests.dbutil import REPO_ROOT

pytestmark = pytest.mark.ped_i07

SRC_ROOT = REPO_ROOT / "src" / "aieos"
SECURITY_ROOT = SRC_ROOT / "platform" / "security"
API_ROOT = SRC_ROOT / "platform" / "api"
RUNTIME_ROOT = SRC_ROOT / "platform" / "runtime"
CONTENT_DOMAIN = SRC_ROOT / "domains" / "content" / "domain"
CONTENT_APP = SRC_ROOT / "domains" / "content" / "application"
BOUNDARY_DOC = REPO_ROOT / "docs" / "PED-I07-TRUSTED-REQUEST-IDENTITY-CONTRACT.md"
PED_I06_DOC = REPO_ROOT / "docs" / "PED-I06-ASGI-OCI-RUNTIME-FOUNDATION.md"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"
MIGRATIONS = REPO_ROOT / "migrations" / "versions"
PYPROJECT = REPO_ROOT / "pyproject.toml"
OCI_DOCKERFILE = REPO_ROOT / "deploy" / "oci" / "Dockerfile.api-runtime-probe"

_FORBIDDEN_IDENTITY_HEADERS = (
    "X-AIEOS-Principal-ID",
    "X-User-ID",
    "X-Actor-ID",
    "X-Roles",
    "X-Permissions",
    "X-Capabilities",
    "X-Admin",
    "X-Superuser",
)

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

# PED-I08 authorizes PyJWT only in platform.security.jwt_bearer.
_JWT_AUTHORIZED_RELATIVE = Path("jwt_bearer.py")

_FORBIDDEN_PRODUCTION_FAKES = (
    "AlwaysAuthenticated",
    "AllowAllAuthorizationKernel",
    "PermissiveAuthorizationKernel",
    "DefaultAuthorizationKernel",
    "StubSecurityContextResolver",
    "FixedPrincipalAuthenticator",
    "MutableCurrentTenantAccessAuthority",
    "AllowReviewAuthorization",
    "AllowPublicationAuthorization",
)


def _py_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*.py") if p.is_file()]


def test_trusted_request_identity_is_immutable_and_minimal() -> None:
    fields = {f.name for f in TrustedRequestIdentity.__dataclass_fields__.values()}
    assert fields == {"principal_id"}
    identity = TrustedRequestIdentity(principal_id=uuid4())
    assert isinstance(identity.principal_id, UUID)
    with pytest.raises(Exception):
        identity.principal_id = uuid4()  # type: ignore[misc]
    assert not hasattr(identity, "roles")
    assert not hasattr(identity, "permissions")
    assert not hasattr(identity, "capabilities")
    assert not hasattr(identity, "tenants")
    assert not hasattr(identity, "is_admin")
    source = (SECURITY_ROOT / "identity.py").read_text(encoding="utf-8")
    for needle in ("roles", "permissions", "capabilities", "token", "password", "jwt"):
        # comments may mention forbidden concepts; field assignment must not exist
        assert f"{needle}:" not in source.lower().replace(" ", "")


def test_trusted_security_context_remains_minimal() -> None:
    fields = {f.name for f in TrustedSecurityContext.__dataclass_fields__.values()}
    assert fields == {"tenant_id", "principal_id"}
    assert not hasattr(
        TrustedSecurityContext(tenant_id=uuid4(), principal_id=uuid4()), "roles"
    )


def test_security_context_resolver_consumes_trusted_identity() -> None:
    sig = inspect.signature(SecurityContextResolver.resolve)
    params = list(sig.parameters)
    assert "identity" in params
    assert "requested_tenant_id" in params
    assert "self" in params
    # Protocol resolve must not take a bare requested_tenant_id-only shape
    assert set(params) >= {"self", "identity", "requested_tenant_id"}


def test_authenticator_and_authority_ports_exist() -> None:
    assert hasattr(RequestIdentityAuthenticator, "authenticate")
    auth_sig = inspect.signature(RequestIdentityAuthenticator.authenticate)
    assert "request" in auth_sig.parameters
    assert hasattr(CurrentTenantAccessAuthority, "authorize_tenant")
    authz_sig = inspect.signature(CurrentTenantAccessAuthority.authorize_tenant)
    assert "principal_id" in authz_sig.parameters
    assert "tenant_id" in authz_sig.parameters


def test_create_app_requires_explicit_authenticator() -> None:
    sig = inspect.signature(create_app)
    param = sig.parameters["request_identity_authenticator"]
    assert param.default is inspect.Parameter.empty
    assert "request_identity_authenticator" in sig.parameters


def test_api_runtime_dependencies_requires_authenticator() -> None:
    sig = inspect.signature(ApiRuntimeDependencies)
    param = sig.parameters["request_identity_authenticator"]
    assert param.default is inspect.Parameter.empty
    with pytest.raises(TypeError):
        ApiRuntimeDependencies()  # type: ignore[call-arg]


def test_current_authority_resolver_fail_closed() -> None:
    class _Deny:
        def authorize_tenant(self, *, principal_id, tenant_id) -> None:
            raise UnauthorizedError("denied")

    class _Broken:
        def authorize_tenant(self, *, principal_id, tenant_id) -> None:
            raise RuntimeError("SECRET_AUTHORITY_BUG")

    identity = TrustedRequestIdentity(principal_id=uuid4())
    tenant = uuid4()
    deny = CurrentAuthoritySecurityContextResolver(_Deny())
    with pytest.raises(UnauthorizedError):
        deny.resolve(identity=identity, requested_tenant_id=tenant)
    with pytest.raises(UnauthenticatedError):
        deny.resolve(identity=identity, requested_tenant_id=None)
    broken = CurrentAuthoritySecurityContextResolver(_Broken())
    with pytest.raises(AuthorizationUnavailableError):
        broken.resolve(identity=identity, requested_tenant_id=tenant)


def test_no_identity_header_shortcuts_in_src() -> None:
    hits: list[str] = []
    for path in _py_files(SRC_ROOT):
        text = path.read_text(encoding="utf-8")
        for header in _FORBIDDEN_IDENTITY_HEADERS:
            if header in text:
                hits.append(f"{path.relative_to(REPO_ROOT)}:{header}")
    assert hits == []


def test_no_jwt_oidc_policy_engine_or_auth_sdk_in_src() -> None:
    """PED-I07 forbid; PED-I08 advances: PyJWT only in jwt_bearer.py."""
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
                        hits.append(f"{path.relative_to(REPO_ROOT)}:import {name}")
                    continue
                for needle in _FORBIDDEN_AUTH_LIBS:
                    if needle.lower() == root:
                        hits.append(f"{path.relative_to(REPO_ROOT)}:import {name}")
    deps = PYPROJECT.read_text(encoding="utf-8").lower()
    assert "pyjwt>=" in deps
    assert "cryptography" in deps
    for needle in ("python-jose", "authlib", "msal", "cerbos", "casbin", "openfga"):
        assert needle not in deps
    assert hits == []


def test_no_tests_import_or_permissive_kernels_in_src() -> None:
    hits: list[str] = []
    for path in _py_files(SRC_ROOT):
        text = path.read_text(encoding="utf-8")
        if "from tests." in text or "import tests" in text or "tests.fakes" in text:
            hits.append(str(path.relative_to(REPO_ROOT)))
        tree = ast.parse(text, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name in _FORBIDDEN_PRODUCTION_FAKES:
                hits.append(f"{path.relative_to(REPO_ROOT)}:class {node.name}")
            if isinstance(node, ast.FunctionDef) and node.name in _FORBIDDEN_PRODUCTION_FAKES:
                hits.append(f"{path.relative_to(REPO_ROOT)}:def {node.name}")
        if "except Exception:" in text and "\n            allow" in text:
            hits.append(f"{path.relative_to(REPO_ROOT)}:except-allow")
    assert hits == []


def test_request_types_not_in_domain_or_content_business_contracts() -> None:
    for root in (CONTENT_DOMAIN, CONTENT_APP):
        for path in _py_files(root):
            text = path.read_text(encoding="utf-8")
            assert "starlette.requests" not in text
            assert "fastapi.Request" not in text
            assert "TrustedRequestIdentity" not in text
            assert "RequestIdentityAuthenticator" not in text


def test_resolver_implementation_does_not_read_headers() -> None:
    authority = (SECURITY_ROOT / "authority.py").read_text(encoding="utf-8")
    context = (SECURITY_ROOT / "context.py").read_text(encoding="utf-8")
    for text in (authority, context):
        assert "request.headers" not in text
        assert "headers.get" not in text
        assert "X-AIEOS-Principal-ID" not in text


def test_health_does_not_invoke_authentication() -> None:
    health = (RUNTIME_ROOT / "health.py").read_text(encoding="utf-8")
    assert "authenticate" not in health
    assert "RequestIdentityAuthenticator" not in health
    assert "TrustedSecurityContext" not in health
    assert "SecurityContextResolver" not in health
    assert "CurrentTenantAccessAuthority" not in health
    assert "X-AIEOS-Tenant-ID" not in health


def test_no_module_level_app_singleton() -> None:
    for path in _py_files(SRC_ROOT):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("app = compose_api_application") or line.startswith(
                "app = create_app"
            ):
                raise AssertionError(f"module-level app singleton in {path}: {line}")


def test_ped_i06_oci_remains_non_production_probe() -> None:
    text = OCI_DOCKERFILE.read_text(encoding="utf-8")
    assert "NON_PRODUCTION_RUNTIME_PROBE" in text
    assert "compose_api_application" not in text
    assert "CMD" in text
    assert "uvicorn --version" in text or "UVICORN" in text.upper() or "version" in text
    doc = PED_I06_DOC.read_text(encoding="utf-8")
    assert "NON_PRODUCTION" in doc
    assert "PED-I07" in doc


def test_no_migration_or_identity_tables() -> None:
    versions = sorted(p.name for p in MIGRATIONS.glob("*.py") if p.name != "__init__.py")
    assert "pedi090001_security_authority.py" in versions
    assert "pedi10b2001_asset_authority_sor.py" in versions
    assert versions[-1] == "tosd080002_classroom_assessment_audit.py"
    for path in MIGRATIONS.rglob("*.py"):
        body = path.read_text(encoding="utf-8")
        assert "pedi070001" not in body
        assert "pedi080001" not in body
        assert "trusted_request_identity" not in body.lower()
        assert "security_context" not in body.lower() or path.name.startswith("saii")


def test_bearer_openapi_scheme_is_aieos_bearer_only() -> None:
    """PED-I08 advances OpenAPI with ADR-AIEOS-030 AIEOSBearerAuth only."""
    openapi = (API_ROOT / "openapi.py").read_text(encoding="utf-8")
    assert "AIEOSBearerAuth" in openapi
    assert "bearerFormat" in openapi
    assert "OAuth2PasswordBearer" not in openapi
    assert "authorizationCode" not in openapi
    assert "HTTPBearer" not in openapi


def test_boundary_doc_and_changelog_and_marker() -> None:
    doc = BOUNDARY_DOC.read_text(encoding="utf-8")
    for statement in (
        "REQUESTED TENANT ≠ TENANT AUTHORITY",
        "AUTHENTICATION ≠ AUTHORIZATION",
        "TENANT MEMBERSHIP ≠ CONTENT CAPABILITY",
        "TOKEN/IDENTITY ASSERTION ≠ CAPABILITY SNAPSHOT",
        "AUTHENTICATION FAILURE → FAIL CLOSED",
        "CURRENT AUTHORITY RECHECKED EACH REQUEST",
    ):
        assert statement in doc
    assert "no IdP selected" in doc.lower() or "IdP" in doc
    assert "JWT" in doc or "OIDC" in doc
    assert "policy engine" in doc.lower()
    assert "NOT AUTHORIZED" in doc
    assert "production-ready" not in doc.lower()
    assert "safe to deploy" not in doc.lower()
    changelog = CHANGELOG.read_text(encoding="utf-8")
    assert "PED-I07 trusted request identity and current-tenant SecurityContext foundation" in changelog
    assert "production authentication complete" not in changelog.lower()
    assert "production identity complete" not in changelog.lower()
    assert "production-ready" not in changelog.lower()
    assert "safe to deploy" not in changelog.lower()
    markers = PYPROJECT.read_text(encoding="utf-8")
    assert "ped_i07:" in markers


def test_unavailable_error_types_exist() -> None:
    assert issubclass(AuthenticationUnavailableError, Exception)
    assert issubclass(AuthorizationUnavailableError, Exception)


def test_security_package_has_no_process_global_membership_cache() -> None:
    for path in _py_files(SECURITY_ROOT):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id.upper().endswith(
                        ("CACHE", "MEMBERSHIPS", "SESSION_STORE")
                    ):
                        raise AssertionError(f"global cache in {path}: {target.id}")

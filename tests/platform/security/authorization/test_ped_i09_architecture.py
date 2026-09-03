"""PED-I09 architecture conformance for ADR-AIEOS-031 Authorization Kernel."""

from __future__ import annotations

import ast
import hashlib
import inspect
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from aieos.domains.content.application.catalog import StaticContentTypeCatalog
from aieos.domains.content.domain.schema import ContentSchemaRegistry
from aieos.platform.api.app import create_app
from aieos.platform.api.openapi import build_openapi, canonical_openapi_json
from aieos.platform.security.authorization import (
    AuthorizationKernel,
    KernelAIGenerationAuthorization,
    KernelContentMigrationAuthorization,
    KernelCurrentTenantAccessAuthority,
    KernelPublicationAuthorization,
    KernelReviewAuthorization,
)
from aieos.platform.security.context import TrustedSecurityContext
from aieos.platform.security.identity import TrustedRequestIdentity
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

pytestmark = pytest.mark.ped_i09

SRC_ROOT = REPO_ROOT / "src" / "aieos"
AUTHZ_ROOT = SRC_ROOT / "platform" / "security" / "authorization"
MIGRATIONS = REPO_ROOT / "migrations" / "versions"
BOUNDARY_DOC = REPO_ROOT / "docs" / "PED-I09-PRODUCTION-AUTHORIZATION-KERNEL.md"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"
PYPROJECT = REPO_ROOT / "pyproject.toml"
UV_LOCK = REPO_ROOT / "uv.lock"
SNAPSHOT = REPO_ROOT / "contracts" / "openapi" / "aieos-v1.json"
COMMON = REPO_ROOT / "tools" / "release" / "common.py"

EXPECTED_OPENAPI_SHA256 = (
    "7D7D0E7C7115667757A31CFEB5474F7498ECC7198FB812DE5EF14A0E9F2D289A"
)

_FORBIDDEN_AUTHZ_LIBS = (
    "opa",
    "rego",
    "cerbos",
    "cedar",
    "casbin",
    "openfga",
    "auth0",
    "keycloak",
    "verifiedpermissions",
)

_FORBIDDEN_TABLE_DDL = (
    "CREATE TABLE security.roles",
    "CREATE TABLE security.role_capabilities",
    "CREATE TABLE security.permissions",
    "CREATE TABLE security.role_permissions",
    "CREATE TABLE security.policy_documents",
    "CREATE TABLE security.policy_bindings",
    "CREATE TABLE security.delegations",
    "CREATE TABLE security.break_glass",
    "CREATE TABLE security.admin_users",
    "CREATE TABLE security.user_roles",
)

_FORBIDDEN_SRC_SYMBOLS = (
    "AllowAllAuthorizationKernel",
    "PermissiveAuthorizationKernel",
    "DefaultAuthorizationKernel",
    "WildcardAuthorization",
    "BreakGlass",
    "is_admin",
)


def _py_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*.py") if p.is_file()]


def test_trusted_identity_and_context_unchanged() -> None:
    assert set(TrustedRequestIdentity.__dataclass_fields__) == {"principal_id"}
    assert set(TrustedSecurityContext.__dataclass_fields__) == {
        "tenant_id",
        "principal_id",
    }


def test_kernel_and_adapters_exist() -> None:
    assert inspect.isclass(AuthorizationKernel)
    assert hasattr(AuthorizationKernel, "decide_tenant_access")
    assert hasattr(AuthorizationKernel, "decide_capability")
    assert hasattr(KernelCurrentTenantAccessAuthority, "authorize_tenant")
    for cls in (
        KernelReviewAuthorization,
        KernelPublicationAuthorization,
        KernelAIGenerationAuthorization,
        KernelContentMigrationAuthorization,
    ):
        assert hasattr(cls, "authorize")


def test_migration_revision_chain() -> None:
    mig = (MIGRATIONS / "pedi090001_security_authority.py").read_text(encoding="utf-8")
    assert 'revision: str = "pedi090001"' in mig
    assert 'down_revision: str | None = "saii020001"' in mig
    for needle in _FORBIDDEN_TABLE_DDL:
        assert needle.lower() not in mig.lower().replace("\n", " ")
    assert "FORCE ROW LEVEL SECURITY" in mig
    assert "security.principals" in mig
    assert "security.tenants" in mig
    assert "security.tenant_memberships" in mig
    assert "security.capability_grants" in mig
    assert "ck_security_capability_grants_capability_no_wildcard" in mig
    assert "position('*' in capability) = 0" in mig
    assert "CREATE SCHEMA security" not in mig


def test_content_capability_vocabulary_owned_by_ports_not_decisions() -> None:
    """Ownership gate: generic decisions.py must not redefine Content constants."""
    decisions_path = AUTHZ_ROOT / "decisions.py"
    decisions = decisions_path.read_text(encoding="utf-8")
    for name in (
        "CONTENT_REVIEW_SUBMIT",
        "CONTENT_REVIEW_DECIDE",
        "CONTENT_PUBLISH",
        "CONTENT_VERSION_CREATE",
        "CONTENT_MIGRATE_IMPORT",
        "AIEOS_CONTENT_CAPABILITIES",
    ):
        assert f"{name} =" not in decisions
    assert '"content.review.submit"' not in decisions
    assert '"content.publish"' not in decisions

    decisions_tree = ast.parse(decisions, filename=str(decisions_path))
    for node in ast.walk(decisions_tree):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules = [node.module]
        for name in modules:
            assert not name.startswith("aieos.domains.content")

    kernel_src = (AUTHZ_ROOT / "kernel.py").read_text(encoding="utf-8")
    kernel_tree = ast.parse(kernel_src, filename=str(AUTHZ_ROOT / "kernel.py"))
    for node in ast.walk(kernel_tree):
        modules = []
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules = [node.module]
        for name in modules:
            assert not name.startswith("aieos.domains.content")
    assert "CONTENT_REVIEW_SUBMIT" not in kernel_src

    adapters = (AUTHZ_ROOT / "content_adapters.py").read_text(encoding="utf-8")
    assert "from aieos.domains.content.application.ports import" in adapters
    for name in (
        "CONTENT_REVIEW_SUBMIT",
        "CONTENT_REVIEW_DECIDE",
        "CONTENT_PUBLISH",
        "CONTENT_VERSION_CREATE",
        "CONTENT_MIGRATE_IMPORT",
    ):
        assert name in adapters
    # Catalog is composed from imported symbols, not parallel string literals.
    assert "AIEOS_CONTENT_CAPABILITIES: frozenset[str] = frozenset(" in adapters
    assert '"content.review.submit"' not in adapters
    assert '"content.publish"' not in adapters

    from aieos.domains.content.application import ports as content_ports
    from aieos.platform.security.authorization.content_adapters import (
        AIEOS_CONTENT_CAPABILITIES,
    )

    assert AIEOS_CONTENT_CAPABILITIES == frozenset(
        {
            content_ports.CONTENT_REVIEW_SUBMIT,
            content_ports.CONTENT_REVIEW_DECIDE,
            content_ports.CONTENT_PUBLISH,
            content_ports.CONTENT_VERSION_CREATE,
            content_ports.CONTENT_MIGRATE_IMPORT,
        }
    )
    # Package re-exports must be the same objects as ports (single source of truth).
    from aieos.platform.security import authorization as authz_pkg

    assert authz_pkg.CONTENT_PUBLISH is content_ports.CONTENT_PUBLISH
    assert authz_pkg.CONTENT_REVIEW_SUBMIT is content_ports.CONTENT_REVIEW_SUBMIT


def test_no_external_authorization_sdk_imports_or_deps() -> None:
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
                if root in _FORBIDDEN_AUTHZ_LIBS:
                    hits.append(f"{path.relative_to(REPO_ROOT)}:{name}")
    assert hits == []
    deps = PYPROJECT.read_text(encoding="utf-8").lower()
    for needle in _FORBIDDEN_AUTHZ_LIBS:
        assert needle not in deps
    # No new authorization dependency lines beyond existing SQLAlchemy/JWT stack.
    assert "casbin" not in UV_LOCK.read_text(encoding="utf-8").lower()
    assert "openfga" not in UV_LOCK.read_text(encoding="utf-8").lower()
    assert "cerbos" not in UV_LOCK.read_text(encoding="utf-8").lower()


def test_no_jwt_business_authority_mapping_in_kernel() -> None:
    for path in _py_files(AUTHZ_ROOT):
        text = path.read_text(encoding="utf-8")
        assert "jwt.decode" not in text
        assert "PyJWK" not in text
        lower = text.lower()
        # Decision code must not map token claims into ALLOW.
        assert "claims.get(\"roles\")" not in lower
        assert "claims.get('roles')" not in lower
        assert "claims.get(\"permissions\")" not in lower
        assert "claims.get(\"scope\")" not in lower


def test_no_wildcard_admin_bypass_delegation_cache_impl() -> None:
    hits: list[str] = []
    for path in _py_files(AUTHZ_ROOT):
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name in _FORBIDDEN_SRC_SYMBOLS:
                hits.append(f"{path.name}:class {node.name}")
            if isinstance(node, ast.FunctionDef) and node.name in _FORBIDDEN_SRC_SYMBOLS:
                hits.append(f"{path.name}:def {node.name}")
        # No decision cache dicts at module level.
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and "CACHE" in target.id.upper():
                        hits.append(f"{path.name}:cache {target.id}")
        if "capability.startswith(" in text or "fnmatch" in text:
            hits.append(f"{path.name}:wildcard-prefix")
        if "BYPASS" in text and "ALLOW" in text and "is_admin" in text.lower():
            hits.append(f"{path.name}:admin-bypass")
    assert hits == []
    # No live delegation table usage in authorization package.
    for path in _py_files(AUTHZ_ROOT):
        body = path.read_text(encoding="utf-8")
        assert "security.delegations" not in body
        assert "delegated_grants" not in body


def test_no_control_plane_crud_routes() -> None:
    routes = (
        SRC_ROOT / "domains" / "content" / "api" / "v1" / "routes.py"
    ).read_text(encoding="utf-8")
    for needle in (
        "/principals",
        "/memberships",
        "/capability-grants",
        "/authorization/decide",
        "/security/tenants",
    ):
        assert needle not in routes


def test_sqlalchemy_not_in_content_domain() -> None:
    domain = SRC_ROOT / "domains" / "content" / "domain"
    for path in _py_files(domain):
        text = path.read_text(encoding="utf-8")
        assert "sqlalchemy" not in text
        assert "AuthorizationKernel" not in text


def test_openapi_unchanged() -> None:
    class _UnusedUowFactory:
        def __call__(self, execution_tenant_id):
            raise AssertionError("uow must not run")

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
    assert digest == EXPECTED_OPENAPI_SHA256
    assert digest in COMMON.read_text(encoding="utf-8")


def test_docs_changelog_marker() -> None:
    doc = BOUNDARY_DOC.read_text(encoding="utf-8")
    for needle in (
        "ADR-AIEOS-031",
        "ALLOW",
        "DENY",
        "security.principals",
        "security.tenants",
        "security.tenant_memberships",
        "security.capability_grants",
        "authorization_unavailable",
        "NOT AUTHORIZED",
        "no roles",
        "no wildcard",
        "no delegation",
        "no break-glass",
        "pedi090001",
        "zero-business-UoW",
    ):
        assert needle.lower() in doc.lower() or needle in doc
    assert "production-ready" not in doc.lower()
    assert "safe to deploy" not in doc.lower()
    changelog = CHANGELOG.read_text(encoding="utf-8")
    assert "PED-I09" in changelog
    assert "production-ready" not in changelog.lower()
    markers = PYPROJECT.read_text(encoding="utf-8")
    assert "ped_i09:" in markers


def test_no_production_allow_all_in_src() -> None:
    hits: list[str] = []
    for path in _py_files(SRC_ROOT):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name in {
                "AllowReviewAuthorization",
                "AllowPublicationAuthorization",
                "MutableCurrentTenantAccessAuthority",
                "AllowAllAuthorizationKernel",
            }:
                hits.append(f"{path.relative_to(REPO_ROOT)}:{node.name}")
    assert hits == []

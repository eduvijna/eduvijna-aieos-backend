"""PED-I10A architecture abuse and contract gates."""

from __future__ import annotations

import ast
import hashlib
import re
from pathlib import Path

import pytest

from aieos.platform.api.openapi import build_openapi, canonical_openapi_json
from aieos.platform.api.app import create_app
from aieos.domains.content.application.catalog import StaticContentTypeCatalog
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

pytestmark = pytest.mark.ped_i10a

SRC_ROOT = REPO_ROOT / "src" / "aieos"
GOV_ROOT = SRC_ROOT / "platform" / "governance"
ASSET_USE = SRC_ROOT / "platform" / "resources" / "asset_use.py"
CONTENT_ADAPTERS = (
    SRC_ROOT / "domains" / "content" / "application" / "asset_authority_adapters.py"
)
MIGRATIONS = REPO_ROOT / "migrations" / "versions"
BOUNDARY_DOC = REPO_ROOT / "docs" / "PED-I10A-PRODUCTION-GOVERNANCE-ADAPTER-FOUNDATION.md"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"
PYPROJECT = REPO_ROOT / "pyproject.toml"
UV_LOCK = REPO_ROOT / "uv.lock"
SNAPSHOT = REPO_ROOT / "contracts" / "openapi" / "aieos-v1.json"
COMPOSITION = SRC_ROOT / "platform" / "runtime" / "composition.py"

EXPECTED_OPENAPI_SHA256 = (
    "230FBDC9323D5C22D6BA7027E74AF977FC7C2EE8C75927D81C5D18C60457B297"
)

_FORBIDDEN_POLICY_ENGINES = (
    "opa",
    "rego",
    "cerbos",
    "cedar",
    "casbin",
    "openfga",
    "auth0",
    "verifiedpermissions",
    "openai",
    "anthropic",
)

_FORBIDDEN_HTTP_CLIENTS = ("httpx", "requests", "aiohttp", "urllib3")


def _py_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*.py") if p.is_file()]


def _import_modules(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return modules


def test_docs_and_marker_present() -> None:
    doc = BOUNDARY_DOC.read_text(encoding="utf-8")
    assert "ADR-AIEOS-032" in doc
    assert "FROZEN" in doc
    assert "095e02d25617aae45f9cdc96cb5a67c8aaa9d6a1" in doc
    assert "Governance != Authorization" in doc or "Authorization != Governance" in doc
    assert "PED-I10B" in doc
    assert "pedi090001" in doc
    changelog = CHANGELOG.read_text(encoding="utf-8")
    assert "PED-I10A" in changelog
    pyproject = PYPROJECT.read_text(encoding="utf-8")
    assert "ped_i10a" in pyproject


def test_openapi_sha_unchanged() -> None:
    from datetime import timedelta
    from uuid import uuid4

    from aieos.domains.content.domain.schema import ContentSchemaRegistry

    class _UnusedUowFactory:
        def __call__(self, execution_tenant_id):  # noqa: ANN001
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



def test_no_new_migration_and_head_remains_pedi090001() -> None:
    names = sorted(p.name for p in MIGRATIONS.glob("*.py") if p.name != "__init__.py")
    assert "pedi090001_security_authority.py" in names
    assert not any(name.startswith("pedi10a") for name in names)
    assert not any("governance" in name.lower() for name in names)
    mig = (MIGRATIONS / "pedi090001_security_authority.py").read_text(encoding="utf-8")
    assert 'revision: str = "pedi090001"' in mig


def test_asset_use_contract_has_no_sqlalchemy_or_http() -> None:
    modules = _import_modules(ASSET_USE)
    blob = "\n".join(modules).lower()
    assert "sqlalchemy" not in blob
    for client in _FORBIDDEN_HTTP_CLIENTS:
        assert client not in blob
    for engine in _FORBIDDEN_POLICY_ENGINES:
        assert engine not in blob
    source = ASSET_USE.read_text(encoding="utf-8")
    assert "create_engine" not in source
    assert "Session" not in source


def test_content_adapters_no_asset_sql_or_policy_engine() -> None:
    modules = _import_modules(CONTENT_ADAPTERS)
    blob = "\n".join(modules).lower()
    assert "sqlalchemy" not in blob
    assert "domains.content.infrastructure" not in blob
    assert "asset_tables" not in blob
    for engine in _FORBIDDEN_POLICY_ENGINES:
        assert engine not in blob
    source = CONTENT_ADAPTERS.read_text(encoding="utf-8")
    assert "SELECT " not in source.upper() or "select" not in source
    assert "content.publish" not in source
    assert "decide_capability" not in source
    assert "role" not in source.lower() or "Authorization" not in source


def test_governance_package_no_policy_engines_or_authz_dump() -> None:
    for path in _py_files(GOV_ROOT):
        modules = _import_modules(path)
        blob = "\n".join(modules).lower()
        for engine in _FORBIDDEN_POLICY_ENGINES:
            assert engine not in blob, f"{path.name}:{engine}"
        assert "security.authorization" not in blob, path.name
        assert "sqlalchemy" not in blob, path.name


def test_production_composition_does_not_wire_test_fakes() -> None:
    source = COMPOSITION.read_text(encoding="utf-8")
    assert "AllowReviewCommentPolicy" not in source
    assert "AllowPublicationGovernance" not in source
    assert "AllowAssetReferenceValidation" not in source
    assert "AllowAssetCurrentGovernance" not in source
    assert "tests.fakes" not in source
    assert "GOVERNANCE_ENABLED" not in source
    assert "RecordingAssetUseAuthority" not in source


def test_no_wildcard_resource_matching_in_adapters() -> None:
    source = CONTENT_ADAPTERS.read_text(encoding="utf-8")
    assert "fnmatch" not in source
    assert "startswith(" not in source
    assert re.search(r'resource_type\s*==\s*["\']\*["\']', source) is None
    assert "*" not in source or 'must be a non-empty exact set' in source


def test_post_publication_boundary_documented() -> None:
    doc = BOUNDARY_DOC.read_text(encoding="utf-8")
    assert "Publication history remains immutable" in doc
    assert "ValidateVersionAssetGovernanceService" in doc
    assert "binary delivery" in doc.lower() or "student" in doc.lower()


def test_uv_lock_unchanged_marker_only_pyproject() -> None:
    # Dependency resolution must stay frozen; marker text is the only allowed edit.
    lock = UV_LOCK.read_text(encoding="utf-8")
    assert lock  # present
    pyproject = PYPROJECT.read_text(encoding="utf-8")
    assert "ped_i10a:" in pyproject

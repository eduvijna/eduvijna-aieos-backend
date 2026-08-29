"""PED-I10B6 architecture, contract, and leakage guards."""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import pytest

from aieos.domains.asset.application.ports import (
    ASSET_CREATE,
    ASSET_LIFECYCLE_MANAGE,
    ASSET_QUARANTINE_MANAGE,
    ASSET_REVISION_ACTIVATE,
    ASSET_REVISION_REGISTER,
    ASSET_SAFETY_DECIDE,
)
from aieos.platform.runtime.activation import FROZEN_API_MUTATION_OPERATION_IDS
from aieos.platform.runtime.readiness import EXPECTED_ALEMBIC_HEAD, _ALL_APP_SCHEMAS
from aieos.platform.security.audit.actions import SecurityAuditAction
from aieos.platform.security.authorization.asset_adapters import (
    AIEOS_ASSET_CAPABILITIES,
)
from tests.dbutil import REPO_ROOT

pytestmark = pytest.mark.ped_i10b6

ASSET_ROOT = REPO_ROOT / "src" / "aieos" / "domains" / "asset"
APPLICATION = ASSET_ROOT / "application"
DOMAIN = ASSET_ROOT / "domain"
PERSISTENCE = ASSET_ROOT / "infrastructure" / "persistence"
ADAPTERS = (
    REPO_ROOT
    / "src"
    / "aieos"
    / "platform"
    / "security"
    / "authorization"
    / "asset_adapters.py"
)
COMPOSITION = REPO_ROOT / "src" / "aieos" / "platform" / "runtime" / "composition.py"
READINESS = REPO_ROOT / "src" / "aieos" / "platform" / "runtime" / "readiness.py"
OPENAPI = REPO_ROOT / "contracts" / "openapi" / "aieos-v1.json"
BOUNDARY_DOC = (
    REPO_ROOT / "docs" / "PED-I10B6-ASSET-AUTHORIZATION-TRANSACTIONAL-AUDIT.md"
)
CHANGELOG = REPO_ROOT / "CHANGELOG.md"
PYPROJECT = REPO_ROOT / "pyproject.toml"
UV_LOCK = REPO_ROOT / "uv.lock"
MIGRATIONS = REPO_ROOT / "migrations" / "versions"
SRC_ROOT = REPO_ROOT / "src"
EXPECTED_OPENAPI_SHA256 = (
    "BBE357612BFF091F7EAF54A4C5F1065B248BB0212A3F0DDF4AFF0685C759C4C7"
)
_CLOUD_NEEDLES = (
    "import boto3",
    "import botocore",
    "from botocore",
    "import minio",
    "from minio",
    "google.cloud.storage",
    "azure.storage.blob",
)
_APPROVED_BLOBSTORE_REL = "src/aieos/domains/asset/infrastructure/blobstore"


def _py_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*.py") if p.is_file()]


def _imported_modules(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return modules


class TestDocsAndContracts:
    def test_docs_changelog_marker_and_classification(self) -> None:
        doc = BOUNDARY_DOC.read_text(encoding="utf-8")
        assert "ADR-AIEOS-036" in doc
        assert "ADR-AIEOS-036R1" in doc
        assert "FROZEN / APPROVED" in doc
        assert "NON_PRODUCTION" in doc
        for capability in sorted(AIEOS_ASSET_CAPABILITIES):
            assert capability in doc
        for action in (
            "asset.create",
            "asset.revision.register",
            "asset.revision.activate",
            "asset.lifecycle.withdraw",
            "asset.lifecycle.restore",
            "asset.lifecycle.delete",
            "asset.quarantine.set",
            "asset.quarantine.clear",
            "asset.safety.pass",
            "asset.safety.fail",
        ):
            assert action in doc
        assert "authorization" in doc.lower()
        assert "Unit of Work" in doc or "UoW" in doc
        assert "resource_revision" in doc
        assert "N→N" in doc or "N -> N" in doc or "after = before" in doc
        assert "no Asset HTTP" in doc or "No Asset HTTP" in doc
        assert "outbox" in doc.lower()
        assert "purge" in doc.lower()
        assert "schema-owner" in doc.lower()
        changelog = CHANGELOG.read_text(encoding="utf-8")
        assert "PED-I10B6" in changelog
        assert "ADR-AIEOS-036" in changelog
        assert "NON_PRODUCTION" in changelog
        assert "ped_i10b6" in PYPROJECT.read_text(encoding="utf-8")

    def test_openapi_lockfile_and_readiness_guard(self) -> None:
        digest = hashlib.sha256(OPENAPI.read_bytes()).hexdigest().upper()
        assert digest == EXPECTED_OPENAPI_SHA256
        assert UV_LOCK.is_file()
        assert EXPECTED_ALEMBIC_HEAD == "tosd040001"
        source = READINESS.read_text(encoding="utf-8")
        assert "asset_schema_owner" not in source
        assert "AIEOS_ASSET_SCHEMA_OWNER_ROLE" not in source
        assert "asset" not in _ALL_APP_SCHEMAS
        assert "asset" not in FROZEN_API_MUTATION_OPERATION_IDS
        openapi = OPENAPI.read_text(encoding="utf-8")
        assert "/assets" not in openapi
        assert "asset.create" not in openapi


class TestBoundaries:
    def test_asset_domain_does_not_import_kernel_sqlalchemy_nats_temporal_content(
        self,
    ) -> None:
        hits: list[str] = []
        for path in _py_files(APPLICATION) + _py_files(DOMAIN):
            for name in _imported_modules(path):
                if name.startswith("aieos.platform.security.authorization.kernel"):
                    hits.append(f"{path.name}:{name}")
                if name.startswith("aieos.platform.security.authorization.asset_adapters"):
                    hits.append(f"{path.name}:{name}")
                if name.split(".")[0] == "sqlalchemy":
                    hits.append(f"{path.name}:{name}")
                if name.startswith("aieos.platform.security.audit.persistence"):
                    hits.append(f"{path.name}:{name}")
                if "nats" in name:
                    hits.append(f"{path.name}:{name}")
                if "temporalio" in name:
                    hits.append(f"{path.name}:{name}")
                if name.startswith("aieos.domains.content"):
                    hits.append(f"{path.name}:{name}")
        assert hits == []

    def test_adapter_imports_canonical_constants_not_literals(self) -> None:
        source = ADAPTERS.read_text(encoding="utf-8")
        assert "from aieos.domains.asset.application.ports import" in source
        for name in (
            "ASSET_CREATE",
            "ASSET_REVISION_REGISTER",
            "ASSET_REVISION_ACTIVATE",
            "ASSET_LIFECYCLE_MANAGE",
            "ASSET_QUARANTINE_MANAGE",
            "ASSET_SAFETY_DECIDE",
        ):
            assert name in source
        assert '"asset.create"' not in source
        assert '"asset.*"' not in source
        assert AIEOS_ASSET_CAPABILITIES == frozenset(
            {
                ASSET_CREATE,
                ASSET_REVISION_REGISTER,
                ASSET_REVISION_ACTIVATE,
                ASSET_LIFECYCLE_MANAGE,
                ASSET_QUARANTINE_MANAGE,
                ASSET_SAFETY_DECIDE,
            }
        )
        assert not any("*" in value for value in AIEOS_ASSET_CAPABILITIES)

    def test_no_asset_http_events_outbox_provider_or_composition(self) -> None:
        assert not (ASSET_ROOT / "api").exists()
        composition = COMPOSITION.read_text(encoding="utf-8")
        assert "AssetMutationService" not in composition
        assert "KernelAssetMutationAuthorization" not in composition
        assert "SqlAlchemyAssetUnitOfWork" not in composition
        assert "domains.asset" not in composition
        for path in _py_files(APPLICATION):
            source = path.read_text(encoding="utf-8")
            assert "APIRouter" not in source
            assert "outbox_messages" not in source
            assert "CloudEvent" not in source
            assert "temporalio" not in source
            assert "nats" not in source
        mutations = (APPLICATION / "mutations.py").read_text(encoding="utf-8")
        assert "self._blob_store.delete" not in mutations
        assert "bytes_purged=True" not in mutations
        assert "INTO asset.deletion_evidence" not in mutations
        hits: list[str] = []
        for path in _py_files(SRC_ROOT):
            source = path.read_text(encoding="utf-8")
            rel = path.relative_to(REPO_ROOT).as_posix()
            in_approved = rel.startswith(_APPROVED_BLOBSTORE_REL + "/")
            for needle in _CLOUD_NEEDLES:
                if needle in source:
                    if in_approved and needle in {
                        "import boto3",
                        "import botocore",
                        "from botocore",
                    }:
                        continue
                    hits.append(f"{rel}:{needle}")
            if "class S3BlobStore" in source or "class MinioBlobStore" in source:
                hits.append(f"{rel}:named-provider")
        assert hits == []

    def test_exact_ten_audit_actions_no_purge_download_upload(self) -> None:
        values = {member.value for member in SecurityAuditAction}
        for required in (
            "asset.create",
            "asset.revision.register",
            "asset.revision.activate",
            "asset.lifecycle.withdraw",
            "asset.lifecycle.restore",
            "asset.lifecycle.delete",
            "asset.quarantine.set",
            "asset.quarantine.clear",
            "asset.safety.pass",
            "asset.safety.fail",
        ):
            assert required in values
        for forbidden in (
            "asset.purge",
            "asset.download",
            "asset.upload",
            "asset.read",
        ):
            assert forbidden not in values

    def test_migration_revision_chain(self) -> None:
        path = MIGRATIONS / "pedi10b6001_asset_security_audit.py"
        source = path.read_text(encoding="utf-8")
        assert 'revision: str = "pedi10b6001"' in source
        assert 'down_revision: str | None = "pedi10b2001"' in source
        assert "CREATE TABLE" not in source
        assert "asset.assets" not in source
        assert "BYPASSRLS" not in source.replace("or add BYPASSRLS", "")
        assert "GRANT UPDATE" not in source
        assert "GRANT DELETE" not in source
        assert "downgrade refused" in source

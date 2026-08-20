"""PED-I10B4 architecture, vocabulary, and leakage guards."""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import pytest

from aieos.platform.resources.asset_use import AssetUseRejectionReason
from aieos.platform.runtime.readiness import EXPECTED_ALEMBIC_HEAD
from tests.dbutil import REPO_ROOT

pytestmark = pytest.mark.ped_i10b4

ASSET_ROOT = REPO_ROOT / "src" / "aieos" / "domains" / "asset"
APPLICATION = ASSET_ROOT / "application"
DOMAIN = ASSET_ROOT / "domain"
PERSISTENCE = ASSET_ROOT / "infrastructure" / "persistence"
CONTENT_ROOT = REPO_ROOT / "src" / "aieos" / "domains" / "content"
SRC_ROOT = REPO_ROOT / "src"
MIGRATIONS = REPO_ROOT / "migrations" / "versions"
COMPOSITION = REPO_ROOT / "src" / "aieos" / "platform" / "runtime" / "composition.py"
READINESS = REPO_ROOT / "src" / "aieos" / "platform" / "runtime" / "readiness.py"
OPENAPI = REPO_ROOT / "contracts" / "openapi" / "aieos-v1.json"
BOUNDARY_DOC = REPO_ROOT / "docs" / "PED-I10B4-ASSET-CURRENT-USE-AUTHORITY.md"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"
PYPROJECT = REPO_ROOT / "pyproject.toml"
UV_LOCK = REPO_ROOT / "uv.lock"
EXPECTED_OPENAPI_SHA256 = (
    "D847C7BC21227072DC2627426A1B61774F33DEB78F65397C7C584BCC38C0BCAF"
)
FROZEN_REASONS = (
    "NOT_FOUND",
    "TENANT_INACCESSIBLE",
    "REVISION_NOT_FOUND",
    "WITHDRAWN",
    "DELETED",
    "QUARANTINED",
    "SAFETY_PENDING",
    "SAFETY_FAILED",
    "BYTES_PURGED",
    "BYTES_MISSING",
    "INTEGRITY_MISMATCH",
)
_FORBIDDEN_REASONS = (
    "BLOB_MISSING",
    "STORAGE_UNAVAILABLE",
    "UNKNOWN",
    "ERROR",
    "CORRUPT",
    "UNAVAILABLE",
    "BLOB_ERROR",
)
_CLOUD_SDK_ROOTS = frozenset({"boto3", "botocore", "minio", "azure", "google"})
_APPROVED_BLOBSTORE_REL = "src/aieos/domains/asset/infrastructure/blobstore"
_CURRENT_USE_FILES = (
    APPLICATION / "use_authority.py",
    PERSISTENCE / "authority_reads.py",
    PERSISTENCE / "postgres_use_authority.py",
    PERSISTENCE / "session.py",
)


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


class TestVocabularyAndDocs:
    def test_rejection_reason_is_adr_034_vocabulary(self) -> None:
        assert tuple(member.value for member in AssetUseRejectionReason) == FROZEN_REASONS
        assert len(AssetUseRejectionReason) == 11
        for forbidden in _FORBIDDEN_REASONS:
            with pytest.raises(ValueError):
                AssetUseRejectionReason(forbidden)

    def test_docs_changelog_marker_and_classification(self) -> None:
        doc = BOUNDARY_DOC.read_text(encoding="utf-8")
        assert "ADR-AIEOS-034" in doc
        assert "FROZEN / APPROVED" in doc
        assert "NON_PRODUCTION" in doc
        for reason in FROZEN_REASONS:
            assert reason in doc
        assert "BYTES_PURGED" in doc
        assert "BYTES_MISSING" in doc
        assert "INTEGRITY_MISMATCH" in doc
        assert "GovernanceUnavailableError" in doc
        assert "current_revision" in doc
        assert "authority_revision" in doc
        assert "observed_at" in doc
        assert "cross-store" in doc.lower() or "Cross-store" in doc
        assert "PED-I09" in doc
        assert "pedi10b2001" in doc
        assert "no production BlobStore" in doc.lower() or "No production BlobStore" in doc
        changelog = CHANGELOG.read_text(encoding="utf-8")
        assert "PED-I10B4" in changelog
        assert "ADR-AIEOS-034" in changelog
        pyproject = PYPROJECT.read_text(encoding="utf-8")
        assert "ped_i10b4" in pyproject


class TestBoundaries:
    def test_content_does_not_import_asset_persistence_or_blobstore(self) -> None:
        hits: list[str] = []
        for path in _py_files(CONTENT_ROOT):
            for name in _imported_modules(path):
                if name.startswith("aieos.domains.asset.infrastructure"):
                    hits.append(f"{path.name}:{name}")
                if name.startswith("aieos.domains.asset.application.blob_store"):
                    hits.append(f"{path.name}:{name}")
                if name.startswith("aieos.domains.asset.infrastructure.persistence"):
                    hits.append(f"{path.name}:{name}")
            source = path.read_text(encoding="utf-8")
            assert "from aieos.domains.asset.infrastructure" not in source
            assert "assets_table" not in source
        adapters = (
            CONTENT_ROOT / "application" / "asset_authority_adapters.py"
        ).read_text(encoding="utf-8")
        assert "AssetUseAuthority" in adapters
        assert "assess_use" in adapters
        assert hits == []

    def test_asset_domain_does_not_import_sqlalchemy_or_content_internals(self) -> None:
        hits: list[str] = []
        for path in _py_files(DOMAIN):
            for name in _imported_modules(path):
                root = name.split(".")[0]
                if root == "sqlalchemy":
                    hits.append(f"{path.name}:{name}")
                if name.startswith("aieos.domains.content"):
                    hits.append(f"{path.name}:{name}")
        for path in _py_files(APPLICATION):
            for name in _imported_modules(path):
                root = name.split(".")[0]
                if root == "sqlalchemy":
                    hits.append(f"{path.name}:{name}")
                if name.startswith("aieos.domains.content.infrastructure"):
                    hits.append(f"{path.name}:{name}")
                if name.startswith("aieos.domains.content.application"):
                    hits.append(f"{path.name}:{name}")
        assert hits == []

    def test_no_cross_domain_fk(self) -> None:
        for path in _py_files(PERSISTENCE):
            source = path.read_text(encoding="utf-8")
            assert "REFERENCES content." not in source
            assert "REFERENCES security." not in source
            assert "content.contents" not in source
            assert "ForeignKey(\"content." not in source

    def test_no_wildcard_resource_type_authority(self) -> None:
        for path in _CURRENT_USE_FILES:
            source = path.read_text(encoding="utf-8")
            assert "fnmatch" not in source
            assert "asset.*" not in source
            assert 'resource_type == "*"' not in source
            assert "startswith(" not in source

    def test_no_storage_key_parsing(self) -> None:
        hits: list[str] = []
        for path in _CURRENT_USE_FILES:
            source = path.read_text(encoding="utf-8")
            if "urlparse" in source or "urllib" in source:
                hits.append(f"{path.name}:urlparse")
            if "storage_key.split(" in source:
                hits.append(f"{path.name}:split")
            if "Path(storage_key)" in source:
                hits.append(f"{path.name}:Path")
            if "storage_key.lower(" in source:
                hits.append(f"{path.name}:lower")
        assert hits == []

    def test_no_production_blobstore_or_cloud_sdk(self) -> None:
        hits: list[str] = []
        for path in _py_files(SRC_ROOT):
            source = path.read_text(encoding="utf-8")
            rel = path.relative_to(REPO_ROOT).as_posix()
            in_approved = rel.startswith(_APPROVED_BLOBSTORE_REL + "/")
            for needle in (
                "import boto3",
                "import botocore",
                "from botocore",
                "import minio",
                "from minio",
                "google.cloud.storage",
                "azure.storage.blob",
            ):
                if needle in source:
                    if in_approved and needle in {
                        "import boto3",
                        "import botocore",
                        "from botocore",
                    }:
                        continue
                    hits.append(f"{rel}:{needle}")
            if "InMemoryBlobStore" in source:
                hits.append(f"{rel}:InMemoryBlobStore")
            if "class S3BlobStore" in source or "class MinioBlobStore" in source:
                hits.append(f"{rel}:named-provider")
        assert hits == []
        for path in _py_files(APPLICATION):
            for name in _imported_modules(path):
                assert name.split(".")[0] not in _CLOUD_SDK_ROOTS

    def test_no_cross_request_positive_cache(self) -> None:
        source = (APPLICATION / "use_authority.py").read_text(encoding="utf-8")
        assert "lru_cache" not in source
        assert "cache_clear" not in source
        assert "functools.cache" not in source
        assert "_positive_cache" not in source

    def test_no_jwt_or_asset_acl_authorization(self) -> None:
        for path in _CURRENT_USE_FILES:
            source = path.read_text(encoding="utf-8")
            assert "import jwt" not in source
            assert "from jwt" not in source
            assert "PyJWT" not in source
            assert "jwks" not in source.lower()
            assert "capability_grants" not in source
            assert "break_glass" not in source
            assert "AssetACL" not in source
            assert "share_grant" not in source
            assert "BYPASSRLS" not in source
            assert "rolbypassrls" not in source.lower()
            assert "DISABLE ROW LEVEL SECURITY" not in source
            assert "TENANT_INACCESSIBLE" not in source

    def test_no_asset_api_events_workflow_or_runtime_composition(self) -> None:
        assert not (ASSET_ROOT / "api").exists()
        assert not (ASSET_ROOT / "workflows").exists()
        composition = COMPOSITION.read_text(encoding="utf-8")
        assert "PostgresAssetUseAuthority" not in composition
        assert "AssetCurrentUseAuthority" not in composition
        assert "InMemoryBlobStore" not in composition
        assert "domains.asset" not in composition
        for path in _py_files(APPLICATION):
            source = path.read_text(encoding="utf-8")
            assert "APIRouter" not in source
            assert "outbox_messages" not in source
            assert "temporalio" not in source

    def test_no_new_migration_head_remains_pedi10b2001(self) -> None:
        assert not any(p.name.startswith("pedi10b4") for p in MIGRATIONS.glob("*.py"))
        assert (MIGRATIONS / "pedi10b2001_asset_authority_sor.py").is_file()
        assert EXPECTED_ALEMBIC_HEAD == "pedi10b6001"

    def test_no_test_blobstore_fake_in_production_source(self) -> None:
        fake = (
            REPO_ROOT
            / "tests"
            / "domains"
            / "asset"
            / "application"
            / "fakes.py"
        )
        assert fake.is_file()
        assert "InMemoryBlobStore" in fake.read_text(encoding="utf-8")
        assert not (APPLICATION / "fakes.py").exists()
        for path in _py_files(SRC_ROOT):
            assert "InMemoryBlobStore" not in path.read_text(encoding="utf-8")

    def test_openapi_and_lockfile_unchanged(self) -> None:
        digest = hashlib.sha256(OPENAPI.read_bytes()).hexdigest().upper()
        assert digest == EXPECTED_OPENAPI_SHA256
        assert UV_LOCK.is_file()
        pyproject = PYPROJECT.read_text(encoding="utf-8")
        assert "boto3==1.40.21" in pyproject
        assert "botocore==1.40.76" in pyproject
        assert "minio" not in pyproject
        assert "google-cloud-storage" not in pyproject

    def test_asset_schema_owner_readiness_guard_remains_open(self) -> None:
        source = READINESS.read_text(encoding="utf-8")
        assert "asset_schema_owner" not in source
        assert "AIEOS_ASSET_SCHEMA_OWNER_ROLE" not in source
        assert "'asset'" not in source or "domains.asset" not in source
        from aieos.platform.runtime.readiness import _ALL_APP_SCHEMAS

        assert "asset" not in _ALL_APP_SCHEMAS

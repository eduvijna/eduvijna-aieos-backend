"""PED-I10B5 architecture, contract, and leakage guards."""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import pytest

from aieos.platform.runtime.readiness import EXPECTED_ALEMBIC_HEAD, _ALL_APP_SCHEMAS
from tests.dbutil import REPO_ROOT

pytestmark = pytest.mark.ped_i10b5

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
BOUNDARY_DOC = REPO_ROOT / "docs" / "PED-I10B5-ASSET-MUTATION-REVISION-ACTIVATION.md"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"
PYPROJECT = REPO_ROOT / "pyproject.toml"
UV_LOCK = REPO_ROOT / "uv.lock"
EXPECTED_OPENAPI_SHA256 = (
    "BBE357612BFF091F7EAF54A4C5F1065B248BB0212A3F0DDF4AFF0685C759C4C7"
)
_MUTATION_IMPL = (
    APPLICATION / "mutations.py",
    APPLICATION / "ports.py",
    APPLICATION / "mutation_errors.py",
    PERSISTENCE / "uow.py",
    PERSISTENCE / "write_repositories.py",
    PERSISTENCE / "errors.py",
)
_CLOUD_SDK_ROOTS = frozenset({"boto3", "botocore", "minio", "azure", "google"})
_APPROVED_BLOBSTORE_REL = "src/aieos/domains/asset/infrastructure/blobstore"
_CLOUD_NEEDLES = (
    "import boto3",
    "import botocore",
    "from botocore",
    "import minio",
    "from minio",
    "google.cloud.storage",
    "azure.storage.blob",
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


class TestDocsAndClassification:
    def test_docs_changelog_marker_and_classification(self) -> None:
        doc = BOUNDARY_DOC.read_text(encoding="utf-8")
        assert "ADR-AIEOS-035" in doc
        assert "FROZEN / APPROVED" in doc
        assert "PED-I10B5" in doc
        assert "NON_PRODUCTION" in doc
        assert "lifecycle" in doc.lower()
        assert "safety" in doc.lower()
        assert "aggregate_revision" in doc
        assert "registration" in doc.lower()
        assert "activation" in doc.lower()
        assert "BlobStore.inspect" in doc
        assert "crash" in doc.lower()
        assert "no purge" in doc.lower() or "No purge" in doc
        assert "no provider" in doc.lower() or "No production BlobStore" in doc
        assert "no api" in doc.lower()
        assert "no production composition" in doc.lower()
        assert "schema-owner" in doc.lower()
        assert "readiness" in doc.lower()
        changelog = CHANGELOG.read_text(encoding="utf-8")
        assert "PED-I10B5" in changelog
        assert "PED-I10B5R1" in changelog
        assert "ADR-AIEOS-035" in changelog
        assert "NON_PRODUCTION" in changelog
        pyproject = PYPROJECT.read_text(encoding="utf-8")
        assert "ped_i10b5" in pyproject


class TestContracts:
    def test_no_pedi10b5_migration_b6_is_current_head(self) -> None:
        assert not any(p.name.startswith("pedi10b5") for p in MIGRATIONS.glob("*.py"))
        assert (MIGRATIONS / "pedi10b2001_asset_authority_sor.py").is_file()
        assert (MIGRATIONS / "pedi10b6001_asset_security_audit.py").is_file()
        assert EXPECTED_ALEMBIC_HEAD == "tosd030001"

    def test_openapi_and_lockfile_unchanged(self) -> None:
        digest = hashlib.sha256(OPENAPI.read_bytes()).hexdigest().upper()
        assert digest == EXPECTED_OPENAPI_SHA256
        assert UV_LOCK.is_file()
        pyproject = PYPROJECT.read_text(encoding="utf-8")
        assert "boto3==1.43.57" in pyproject
        assert "botocore==1.43.57" in pyproject
        assert "minio" not in pyproject
        assert "google-cloud-storage" not in pyproject
        assert "azure-storage" not in pyproject

    def test_asset_schema_owner_readiness_guard_remains_open(self) -> None:
        source = READINESS.read_text(encoding="utf-8")
        assert "asset_schema_owner" not in source
        assert "AIEOS_ASSET_SCHEMA_OWNER_ROLE" not in source
        assert "asset" not in _ALL_APP_SCHEMAS


class TestBoundaries:
    def test_application_has_no_sqlalchemy_or_content_imports(self) -> None:
        hits: list[str] = []
        for path in _py_files(APPLICATION):
            for name in _imported_modules(path):
                root = name.split(".")[0]
                if root == "sqlalchemy":
                    hits.append(f"{path.name}:{name}")
                if name.startswith("aieos.domains.content"):
                    hits.append(f"{path.name}:{name}")
        for path in _py_files(DOMAIN):
            for name in _imported_modules(path):
                if name.split(".")[0] == "sqlalchemy":
                    hits.append(f"{path.name}:{name}")
        assert hits == []

    def test_no_cross_domain_fk_or_sql(self) -> None:
        for path in _py_files(PERSISTENCE):
            source = path.read_text(encoding="utf-8")
            assert "REFERENCES content." not in source
            assert "REFERENCES security." not in source
            assert "content.contents" not in source
            assert 'ForeignKey("content.' not in source
            assert "FROM content." not in source
            assert "INTO content." not in source
        for path in _py_files(CONTENT_ROOT / "infrastructure"):
            source = path.read_text(encoding="utf-8")
            assert "REFERENCES asset." not in source
            assert "FROM asset." not in source
            assert "INTO asset." not in source

    def test_no_production_blobstore_or_cloud_sdk(self) -> None:
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
            if "InMemoryBlobStore" in source:
                hits.append(f"{rel}:InMemoryBlobStore")
            if "class S3BlobStore" in source or "class MinioBlobStore" in source:
                hits.append(f"{rel}:named-provider")
        assert hits == []
        for path in _MUTATION_IMPL:
            for name in _imported_modules(path):
                assert name.split(".")[0] not in _CLOUD_SDK_ROOTS

    def test_no_asset_api_events_workflow_or_runtime_composition(self) -> None:
        assert not (ASSET_ROOT / "api").exists()
        assert not (ASSET_ROOT / "workflows").exists()
        composition = COMPOSITION.read_text(encoding="utf-8")
        assert "AssetMutationService" not in composition
        assert "SqlAlchemyAssetUnitOfWork" not in composition
        assert "PostgresAssetUseAuthority" not in composition
        assert "domains.asset" not in composition
        for path in _py_files(APPLICATION):
            source = path.read_text(encoding="utf-8")
            assert "APIRouter" not in source
            assert "outbox_messages" not in source
            assert "temporalio" not in source
            assert "nats" not in source
            assert "Idempotency-Key" not in source

    def test_no_jwt_or_asset_acl_or_owner_bypass(self) -> None:
        for path in _MUTATION_IMPL:
            source = path.read_text(encoding="utf-8")
            assert "import jwt" not in source
            assert "from jwt" not in source
            assert "jwks" not in source.lower()
            assert "capability_grants" not in source
            assert "AssetACL" not in source
            assert "break_glass" not in source
            assert "BYPASSRLS" not in source or "No BYPASSRLS" in source
            assert "SET ROLE" not in source or "No SET ROLE" in source
            assert "DISABLE ROW LEVEL SECURITY" not in source

    def test_no_purge_bytes_purged_true_or_deletion_evidence_write(self) -> None:
        for path in _MUTATION_IMPL:
            source = path.read_text(encoding="utf-8")
            assert "bytes_purged=True" not in source
            assert "bytes_purged = True" not in source
            assert "bytes_purged=true" not in source.lower().replace(" ", "") or (
                "bytes_purged=false" in source.lower().replace(" ", "")
            )
            assert "deletion_evidence_table" not in source
            assert "INTO asset.deletion_evidence" not in source
            assert "blob_store.delete" not in source
        mutations = (APPLICATION / "mutations.py").read_text(encoding="utf-8")
        assert "self._blob_store.delete" not in mutations
        assert ".delete(" not in mutations
        repos = (PERSISTENCE / "write_repositories.py").read_text(encoding="utf-8")
        assert "bytes_purged=False" in repos
        assert ".commit(" not in repos
        assert ".rollback(" not in repos

    def test_activation_inspects_before_write_lock(self) -> None:
        source = (APPLICATION / "mutations.py").read_text(encoding="utf-8")
        start = source.index("def activate_revision")
        end = source.index("def withdraw_asset")
        body = source[start:end]
        assert body.index("candidate.asset.aggregate_revision") < body.index(
            "self._blob_store.inspect"
        )
        assert body.index("self._blob_store.inspect") < body.index("get_for_update")
        assert "except Exception" not in body
        assert "except RuntimeError" not in body
        facts = source[source.index("def _same_activation_facts") :]
        assert (
            "locked.aggregate_revision == candidate.asset.aggregate_revision" in facts
        )

    def test_uow_installs_transaction_local_tenant_and_always_rolls_back(self) -> None:
        source = (PERSISTENCE / "uow.py").read_text(encoding="utf-8")
        assert "set_config('aieos.tenant_id'" in source
        assert "true)" in source
        assert "rollback" in source
        assert "BYPASSRLS" not in source.replace("No BYPASSRLS", "")
        assert "SET ROLE" not in source.replace("No SET ROLE", "")
        assert "SET LOCAL ROLE" not in source

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
        assert not (APPLICATION / "fakes.py").exists()
        for path in _py_files(SRC_ROOT):
            assert "InMemoryBlobStore" not in path.read_text(encoding="utf-8")
            assert "InMemoryAssetUnitOfWork" not in path.read_text(encoding="utf-8")

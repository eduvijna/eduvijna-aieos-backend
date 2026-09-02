"""PED-I10B3 architecture, scope, and leakage guards."""

from __future__ import annotations

import ast
import hashlib
import inspect
from pathlib import Path

import pytest

from aieos.domains.asset.application.blob_store import BlobStore, Uuid7StorageKeyFactory
from aieos.domains.asset.application.ingest import BlobIngestPreparer, PreparedBlob
from aieos.domains.asset.application.reconciliation import (
    BlobReferenceStatus,
    BlobReconciler,
)
from aieos.platform.runtime.readiness import EXPECTED_ALEMBIC_HEAD
from tests.dbutil import REPO_ROOT

pytestmark = pytest.mark.ped_i10b3

ASSET_ROOT = REPO_ROOT / "src" / "aieos" / "domains" / "asset"
APPLICATION = ASSET_ROOT / "application"
BLOBSTORE_INFRA = ASSET_ROOT / "infrastructure" / "blobstore"
SRC_ROOT = REPO_ROOT / "src"
MIGRATIONS = REPO_ROOT / "migrations" / "versions"
COMPOSITION = REPO_ROOT / "src" / "aieos" / "platform" / "runtime" / "composition.py"
OPENAPI = REPO_ROOT / "contracts" / "openapi" / "aieos-v1.json"
_APPROVED_BLOBSTORE_REL = "src/aieos/domains/asset/infrastructure/blobstore"
EXPECTED_OPENAPI_SHA256 = (
    "CCD233062672B36A4DB6C6B60E7413AF8EEC6FDAAE9550270C6879E4C4A06D7C"
)
CONTENT_ROOT = REPO_ROOT / "src" / "aieos" / "domains" / "content"

_FORBIDDEN_IMPORT_ROOTS = frozenset(
    {
        "boto3",
        "botocore",
        "minio",
        "google",
        "azure",
        "s3fs",
        "fsspec",
        "sqlalchemy",
        "alembic",
        "fastapi",
        "temporalio",
        "nats",
        "requests",
        "httpx",
        "pathlib",
    }
)
_PROVIDER_NEEDLES = (
    "boto3",
    "botocore",
    "minio",
    "google.cloud",
    "azure.storage",
    "s3fs",
    "fsspec",
    "s3://",
    "gs://",
    "azure://",
    "signed_url",
    "public_url",
    "presign",
    "cdn_url",
)
_FAKE_NEEDLES = (
    "InMemoryBlobStore",
    "class MemoryBlobStore",
    "class FilesystemBlobStore",
    "class S3BlobStore",
    "class MinioBlobStore",
    "class AzureBlobStore",
    "class GcsBlobStore",
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


class TestArchitectureScope:
    def test_no_production_blobstore_implementation(self) -> None:
        """Concrete BlobStore may exist only under approved AIStor infrastructure."""
        protocol_found = False
        concrete: list[str] = []
        allowed = {"AiStorBlobStore"}
        for path in _py_files(SRC_ROOT):
            rel = path.relative_to(REPO_ROOT).as_posix()
            in_approved = rel.startswith(_APPROVED_BLOBSTORE_REL + "/") or rel == (
                _APPROVED_BLOBSTORE_REL + "/__init__.py"
            )
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                bases = [
                    ast.unparse(base) if hasattr(ast, "unparse") else ""
                    for base in node.bases
                ]
                if node.name == "BlobStore":
                    assert "Protocol" in " ".join(bases), path
                    protocol_found = True
                if node.name.endswith("BlobStore") and node.name != "BlobStore":
                    if node.name in allowed and in_approved:
                        continue
                    concrete.append(f"{rel}:{node.name}")
                if node.name in {
                    "InMemoryBlobStore",
                    "FilesystemBlobStore",
                    "S3BlobStore",
                    "MinioBlobStore",
                    "AzureBlobStore",
                    "GcsBlobStore",
                }:
                    concrete.append(f"{rel}:{node.name}")
        assert protocol_found
        assert concrete == []
        assert (BLOBSTORE_INFRA / "aistor.py").is_file()

    def test_application_has_no_forbidden_imports_or_provider_needles(self) -> None:
        hits: list[str] = []
        for path in _py_files(APPLICATION):
            for name in _imported_modules(path):
                root = name.split(".")[0]
                if root in _FORBIDDEN_IMPORT_ROOTS:
                    hits.append(f"{path.name}:import {name}")
                if name.startswith("aieos.domains.content"):
                    hits.append(f"{path.name}:content-coupling:{name}")
            source = path.read_text(encoding="utf-8")
            for needle in _PROVIDER_NEEDLES:
                if needle in source:
                    hits.append(f"{path.name}:{needle}")
            for needle in _FAKE_NEEDLES:
                if needle in source:
                    hits.append(f"{path.name}:{needle}")
        assert hits == []

    def test_src_has_no_cloud_sdk_or_in_memory_fake(self) -> None:
        """Provider SDK imports allowed only under Asset infrastructure/blobstore."""
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
                "import s3fs",
                "import fsspec",
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
        assert hits == []

    def test_no_asset_use_authority_protocol_in_asset_domain(self) -> None:
        for path in _py_files(ASSET_ROOT):
            source = path.read_text(encoding="utf-8")
            assert "class AssetUseAuthority" not in source
            if path.name == "use_authority.py":
                assert "def assess_use(" in source
                continue
            assert "def assess_use(" not in source

    def test_no_repository_uow_api_events_or_workflow(self) -> None:
        for path in _py_files(APPLICATION):
            source = path.read_text(encoding="utf-8")
            assert "class SqlAlchemyAssetRepository" not in source
            if path.name == "ports.py":
                assert "class AssetUnitOfWork(Protocol)" in source
            else:
                assert "class AssetUnitOfWork" not in source
            assert "APIRouter" not in source
            assert "outbox_messages" not in source
            assert "temporalio" not in source
            assert "nats" not in source
        assert not (ASSET_ROOT / "api").exists()
        assert not (ASSET_ROOT / "workflows").exists()
        persistence = ASSET_ROOT / "infrastructure" / "persistence"
        names = {p.name for p in _py_files(persistence)}
        assert "repository.py" not in names
        assert "uow.py" in names
        assert "write_repositories.py" in names

    def test_no_migration_and_alembic_head_unchanged(self) -> None:
        assert not any(p.name.startswith("pedi10b3") for p in MIGRATIONS.glob("*.py"))
        assert (MIGRATIONS / "pedi10b2001_asset_authority_sor.py").is_file()
        assert EXPECTED_ALEMBIC_HEAD == "tosd070001"

    def test_no_asset_persistence_or_content_or_composition_change(self) -> None:
        models = (
            ASSET_ROOT / "infrastructure" / "persistence" / "models.py"
        ).read_text(encoding="utf-8")
        assert "blob_exists" not in models
        composition = COMPOSITION.read_text(encoding="utf-8")
        assert "domains.asset" not in composition
        assert "BlobStore" not in composition
        assert "BlobIngestPreparer" not in composition
        assert "InMemoryBlobStore" not in composition
        dumped = OPENAPI.read_bytes()
        digest = hashlib.sha256(dumped).hexdigest().upper()
        assert digest == EXPECTED_OPENAPI_SHA256
        for path in _py_files(CONTENT_ROOT):
            modules = _imported_modules(path)
            for name in modules:
                assert not name.startswith("aieos.domains.asset.application")

    def test_ingest_does_not_persist_or_compensate(self) -> None:
        source = (APPLICATION / "ingest.py").read_text(encoding="utf-8")
        assert "self._blob_store.delete" not in source
        assert "sqlalchemy" not in source
        assert "AssetId(" not in source
        assert "AssetRevision(" not in source
        assert "AssetRevisionId(" not in source
        assert "safety_state" not in source
        assert "current_revision" in source  # documented as NOT mutated
        assert "MUST NOT implement" in source
        assert "delete_blob()" in source
        signature = inspect.signature(BlobIngestPreparer.prepare)
        assert list(signature.parameters) == ["self", "source", "byte_size"]
        assert signature.parameters["byte_size"].kind is inspect.Parameter.KEYWORD_ONLY
        assert {f.name for f in PreparedBlob.__dataclass_fields__.values()} == {
            "storage_key",
            "byte_size",
            "sha256",
        }

    def test_reconciler_is_read_only(self) -> None:
        source = (APPLICATION / "reconciliation.py").read_text(encoding="utf-8")
        assert "sqlalchemy" not in source
        assert "self._inventory.delete" not in source
        assert ".delete(" not in source
        assert "deletion_evidence" not in source
        assert "bytes_purged" not in source
        assert set(BlobReferenceStatus) == {
            BlobReferenceStatus.MATCH,
            BlobReferenceStatus.MISSING,
            BlobReferenceStatus.INTEGRITY_MISMATCH,
        }
        for forbidden in (
            "USABLE",
            "WITHDRAWN",
            "QUARANTINED",
            "SAFETY_FAILED",
        ):
            assert forbidden not in BlobReferenceStatus.__members__
        signature = inspect.signature(BlobReconciler.reconcile)
        assert list(signature.parameters) == ["self"]

    def test_storage_key_is_not_parsed_as_path_or_url(self) -> None:
        hits: list[str] = []
        for path in _py_files(APPLICATION):
            source = path.read_text(encoding="utf-8")
            if "urllib" in source or "urlparse" in source:
                hits.append(f"{path.name}:urlparse")
            if "storage_key.split(" in source:
                hits.append(f"{path.name}:split")
            if "storage_key.lower(" in source:
                hits.append(f"{path.name}:lower")
            if "storage_key.strip()" in source and "return value" not in source:
                # emptiness check uses value.strip(); accepted keys must not be rewritten
                if "object.__setattr__(self, \"storage_key\", value.strip())" in source:
                    hits.append(f"{path.name}:canonical-strip")
        assert hits == []
        key = Uuid7StorageKeyFactory().generate()
        assert "/" not in key
        assert "://" not in key

    def test_blobstore_protocol_operations(self) -> None:
        source = (APPLICATION / "blob_store.py").read_text(encoding="utf-8")
        assert "def create(" in source
        assert "def inspect(" in source
        assert "def delete(" in source
        assert "def overwrite" not in source
        assert "def replace" not in source
        assert "def rename" not in source
        assert "def move" not in source
        assert "def public_url" not in source
        assert "def signed_url" not in source
        assert "def presign" not in source
        create_params = inspect.signature(BlobStore.create).parameters
        assert create_params.keys() >= {"storage_key", "source", "byte_size"}
        assert create_params["byte_size"].kind is inspect.Parameter.KEYWORD_ONLY
        assert create_params["storage_key"].kind is inspect.Parameter.KEYWORD_ONLY
        assert create_params["source"].kind is inspect.Parameter.KEYWORD_ONLY

    def test_fake_lives_only_under_tests(self) -> None:
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
        assert not (ASSET_ROOT / "infrastructure" / "blob.py").exists()
        assert not (ASSET_ROOT / "infrastructure" / "storage").exists()

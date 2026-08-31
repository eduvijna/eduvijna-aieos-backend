"""PED-I10B2 architecture, SQLAlchemy conformance, and scope guards."""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import pytest
from sqlalchemy import BigInteger, Boolean, DateTime, Text
from sqlalchemy.dialects.postgresql import UUID

from aieos.domains.asset.infrastructure.persistence.metadata import (
    ASSET_SCHEMA,
    asset_metadata,
)
from aieos.domains.asset.infrastructure.persistence.models import (
    asset_revision_states_table,
    asset_revisions_table,
    assets_table,
    deletion_evidence_table,
)
from aieos.platform.runtime.readiness import EXPECTED_ALEMBIC_HEAD
from tests.dbutil import REPO_ROOT

pytestmark = pytest.mark.ped_i10b2

ASSET_ROOT = REPO_ROOT / "src" / "aieos" / "domains" / "asset"
ASSET_DOMAIN = ASSET_ROOT / "domain"
ASSET_PERSISTENCE = ASSET_ROOT / "infrastructure" / "persistence"
MIGRATIONS = REPO_ROOT / "migrations" / "versions"
COMPOSITION = REPO_ROOT / "src" / "aieos" / "platform" / "runtime" / "composition.py"
OPENAPI = REPO_ROOT / "contracts" / "openapi" / "aieos-v1.json"
EXPECTED_OPENAPI_SHA256 = (
    "230FBDC9323D5C22D6BA7027E74AF977FC7C2EE8C75927D81C5D18C60457B297"
)
_FORBIDDEN_IMPORT_ROOTS = frozenset(
    {
        "fastapi",
        "sqlalchemy",
        "alembic",
        "temporalio",
        "nats",
        "boto3",
        "botocore",
        "azure",
        "google",
        "minio",
    }
)
_CLOUD_NEEDLES = (
    "boto3",
    "botocore",
    "google.cloud",
    "azure.storage",
    "minio",
    "s3://",
)
_APPROVED_BLOBSTORE_REL = "infrastructure/blobstore"
_FORBIDDEN_ASSET_DIRS = (
    "api",
    "workflows",
    "blob",
    "storage",
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


class TestSqlAlchemyConformance:
    def test_metadata_schema_and_tables(self) -> None:
        assert ASSET_SCHEMA == "asset"
        assert asset_metadata.schema == "asset"
        names = set(asset_metadata.tables)
        assert "asset.assets" in names
        assert "asset.asset_revisions" in names
        assert "asset.asset_revision_states" in names
        assert "asset.deletion_evidence" in names
        assert assets_table.name == "assets"
        assert asset_revisions_table.name == "asset_revisions"
        assert asset_revision_states_table.name == "asset_revision_states"
        assert deletion_evidence_table.name == "deletion_evidence"

    def test_column_types_and_nullability(self) -> None:
        assert isinstance(assets_table.c.asset_id.type, UUID)
        assert assets_table.c.asset_id.type.as_uuid is True
        assert assets_table.c.current_revision.nullable is True
        assert isinstance(assets_table.c.current_revision.type, BigInteger)
        assert assets_table.c.aggregate_revision.nullable is False
        assert isinstance(assets_table.c.created_at.type, DateTime)
        assert assets_table.c.created_at.type.timezone is True
        assert isinstance(asset_revisions_table.c.storage_key.type, Text)
        assert asset_revisions_table.c.sha256.type.length == 64
        assert isinstance(asset_revision_states_table.c.bytes_purged.type, Boolean)
        assert asset_revision_states_table.c.bytes_purged.nullable is False

    def test_constraints_match_migration_names(self) -> None:
        asset_constraint_names = {c.name for c in assets_table.constraints}
        assert "pk_assets" in asset_constraint_names
        assert "uq_assets_tenant_asset" in asset_constraint_names
        assert "uq_assets_tenant_asset_resource_type" in asset_constraint_names
        assert "fk_assets_current_revision" in asset_constraint_names
        revision_names = {c.name for c in asset_revisions_table.constraints}
        assert "uq_asset_revisions_tenant_asset_number" in revision_names
        assert "uq_asset_revisions_tenant_asset_id_number" in revision_names
        assert "fk_asset_revisions_asset_resource" in revision_names
        current_fk = next(
            c
            for c in assets_table.constraints
            if c.name == "fk_assets_current_revision"
        )
        assert current_fk.deferrable is True
        assert current_fk.initially == "DEFERRED"
        assert current_fk.ondelete == "RESTRICT"

    def test_no_create_all_or_content_persistence_import(self) -> None:
        for path in _py_files(ASSET_PERSISTENCE):
            source = path.read_text(encoding="utf-8")
            assert "create_all(" not in source
            assert "metadata.create_all" not in source
            modules = _imported_modules(path)
            for name in modules:
                assert not name.startswith(
                    "aieos.domains.content.infrastructure.persistence"
                )
                assert "content_metadata" not in name
            assert "content_metadata" not in source


class TestArchitectureScope:
    def test_domain_has_no_persistence_or_cloud_imports(self) -> None:
        hits: list[str] = []
        for path in _py_files(ASSET_DOMAIN):
            for name in _imported_modules(path):
                root = name.split(".")[0]
                if root in _FORBIDDEN_IMPORT_ROOTS:
                    hits.append(f"{path.name}:{name}")
                if name.startswith("aieos.domains.content"):
                    hits.append(f"{path.name}:content-coupling:{name}")
            source = path.read_text(encoding="utf-8")
            for needle in _CLOUD_NEEDLES:
                if needle in source:
                    hits.append(f"{path.name}:{needle}")
        assert hits == []

    def test_asset_source_has_no_cloud_sdk_or_out_of_scope_packages(self) -> None:
        hits: list[str] = []
        for path in _py_files(ASSET_ROOT):
            rel = path.relative_to(ASSET_ROOT).as_posix()
            in_approved = rel.startswith(_APPROVED_BLOBSTORE_REL + "/")
            source = path.read_text(encoding="utf-8")
            for needle in _CLOUD_NEEDLES:
                if needle in source:
                    if in_approved and needle in {"boto3", "botocore"}:
                        continue
                    hits.append(f"{rel}:{needle}")
            for name in _imported_modules(path):
                root = name.split(".")[0]
                if root in {"boto3", "botocore", "minio"}:
                    if in_approved and root in {"boto3", "botocore"}:
                        continue
                    hits.append(f"{path.name}:import {name}")
        assert hits == []
        for dirname in _FORBIDDEN_ASSET_DIRS:
            assert not (ASSET_ROOT / dirname).exists()
        persistence_files = {p.name for p in _py_files(ASSET_PERSISTENCE)}
        assert "repository.py" not in persistence_files
        assert "repositories.py" not in persistence_files
        assert "write_repositories.py" in persistence_files
        assert "uow.py" in persistence_files
        for path in _py_files(ASSET_ROOT):
            source = path.read_text(encoding="utf-8")
            assert "class SqlAlchemyAssetRepository" not in source
            if path.name == "ports.py":
                assert "class AssetUnitOfWork(Protocol)" in source
            else:
                assert "class AssetUnitOfWork" not in source
            if path.name == "blob_store.py" and path.parent.name == "application":
                assert "class BlobStore(Protocol)" in source
            else:
                assert "class BlobStore(" not in source
            assert "class AssetUseAuthority" not in source
            assert "APIRouter" not in source
            assert "outbox_messages" not in source

    def test_no_composition_or_openapi_change(self) -> None:
        composition = COMPOSITION.read_text(encoding="utf-8")
        assert "domains.asset" not in composition
        assert "asset_metadata" not in composition
        dumped = OPENAPI.read_bytes()
        digest = hashlib.sha256(dumped).hexdigest().upper()
        assert digest == EXPECTED_OPENAPI_SHA256
        assert EXPECTED_ALEMBIC_HEAD == "tosd060001"

    def test_exactly_one_new_migration_and_no_wildcard_resource_match(self) -> None:
        asset_migrations = [
            p.name for p in MIGRATIONS.glob("pedi10b2*.py")
        ]
        assert asset_migrations == ["pedi10b2001_asset_authority_sor.py"]
        source = (
            MIGRATIONS / "pedi10b2001_asset_authority_sor.py"
        ).read_text(encoding="utf-8")
        assert 'revision: str = "pedi10b2001"' in source
        assert 'down_revision: str | None = "pedi090001"' in source
        assert "branch_labels" in source
        assert "LIKE 'asset.%'" not in source
        assert "resource_type LIKE" not in source
        assert "fnmatch" not in source
        assert "ON UPDATE CASCADE" not in source
        assert "ON DELETE CASCADE" not in source
        assert "content." not in source or "Generic Content schema-owner" in source
        assert "REFERENCES content." not in source
        assert "REFERENCES security." not in source
        for path in _py_files(ASSET_PERSISTENCE):
            text = path.read_text(encoding="utf-8")
            assert "fnmatch" not in text
            assert 'startswith("asset.")' not in text
            assert "storage_url" not in text
            assert "cdn_url" not in text

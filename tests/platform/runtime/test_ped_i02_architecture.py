"""PED-I02 architecture boundaries for API DB readiness."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from aieos.platform.runtime.readiness import EXPECTED_ALEMBIC_HEAD
from tests.dbutil import REPO_ROOT

pytestmark = pytest.mark.ped_i02

RUNTIME_ROOT = REPO_ROOT / "src" / "aieos" / "platform" / "runtime"
BOUNDARY_DOC = REPO_ROOT / "docs" / "PED-I02-API-DB-READINESS-CONTRACT.md"
PRIVILEGE_DOC = REPO_ROOT / "docs" / "GCI-I02-DATABASE-PRIVILEGE-CONTRACT.md"
PED_I01_DOC = REPO_ROOT / "docs" / "PED-I01-PRODUCTION-RUNTIME-CONFIG-CONTRACT.md"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"
MIGRATIONS = REPO_ROOT / "migrations" / "versions"

_FORBIDDEN_IMPORT_NEEDLES = (
    "tests.",
    "dotenv",
    "uvicorn",
    "gunicorn",
    "hypercorn",
    "temporalio",
    "nats",
    "boto3",
    "botocore",
    "kubernetes",
    "google.cloud",
    "azure.",
)


def _py_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*.py") if p.is_file()]


def test_expected_alembic_head_matches_script_directory() -> None:
    assert EXPECTED_ALEMBIC_HEAD == "tosd040001"
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()
    assert heads == [EXPECTED_ALEMBIC_HEAD]


def test_readiness_and_health_import_boundary() -> None:
    for name in ("readiness.py", "health.py", "database.py"):
        text = (RUNTIME_ROOT / name).read_text(encoding="utf-8")
        for needle in _FORBIDDEN_IMPORT_NEEDLES:
            assert needle not in text, f"{name}: {needle}"
        assert "AIEOS_DATABASE_URL" not in text
        assert "command.upgrade" not in text
        assert "alembic.command" not in text


def test_readiness_has_no_mutation_tenant_or_set_role() -> None:
    readiness = (RUNTIME_ROOT / "readiness.py").read_text(encoding="utf-8")
    assert "SET ROLE" not in readiness
    assert "set_role" not in readiness.lower()
    assert "aieos.tenant_id" not in readiness
    assert "MUTATIONS_ENABLED" not in readiness
    assert "AlwaysReady" not in readiness
    for verb in ("INSERT INTO", "UPDATE ", "DELETE FROM", "TRUNCATE "):
        assert verb not in readiness.upper()


def test_no_nats_temporal_required_for_api_readiness() -> None:
    readiness = (RUNTIME_ROOT / "readiness.py").read_text(encoding="utf-8")
    health = (RUNTIME_ROOT / "health.py").read_text(encoding="utf-8")
    for path_text in (readiness, health):
        assert "import nats" not in path_text
        assert "from nats" not in path_text
        assert "temporalio" not in path_text
        assert "from temporal" not in path_text
    doc = BOUNDARY_DOC.read_text(encoding="utf-8")
    assert "NATS" in doc
    assert "Temporal" in doc
    assert "does **not** require" in doc or "What readiness does" in doc


def test_no_permissive_production_ready_default() -> None:
    for path in _py_files(RUNTIME_ROOT):
        text = path.read_text(encoding="utf-8")
        assert "AlwaysReadyProbe" not in text
        assert "ready=True" not in text.replace("ready: bool", "")
        assert "READINESS_DISABLED" not in text


def test_migration_head_unchanged_no_pedi02_migration() -> None:
    versions = sorted(p.name for p in MIGRATIONS.glob("*.py") if p.name != "__init__.py")
    assert "pedi090001_security_authority.py" in versions
    assert "pedi10b2001_asset_authority_sor.py" in versions
    assert versions[-1] == "tosd040001_multi_artifact_provenance_and_generation_fences.py"
    for path in MIGRATIONS.rglob("*.py"):
        body = path.read_text(encoding="utf-8")
        assert "pedi020001" not in body


def test_docs_and_changelog() -> None:
    doc = BOUNDARY_DOC.read_text(encoding="utf-8")
    assert "PED-I02" in doc
    assert "API runtime database/readiness" in doc.lower() or "readiness" in doc.lower()
    assert "/livez" in doc
    assert "/readyz" in doc
    assert "PostgreSQL" in doc and "18" in doc
    assert "pedi090001" in doc
    assert "NOT AUTHORIZED" in doc
    assert "production ready" not in doc.lower()
    assert "safe to deploy" not in doc.lower()
    assert "production-ready" not in doc.lower()
    assert "TLS" in doc
    privilege = PRIVILEGE_DOC.read_text(encoding="utf-8")
    assert "alembic_version" in privilege
    assert "USAGE" in privilege and "public" in privilege
    assert "migration authority" in privilege.lower()
    ped_i01 = PED_I01_DOC.read_text(encoding="utf-8")
    assert "AIEOS_RUNTIME_DATABASE_CONNECT_TIMEOUT_SECONDS" in ped_i01
    changelog = CHANGELOG.read_text(encoding="utf-8")
    assert "PED-I02" in changelog
    assert "API runtime database/readiness foundation" in changelog
    assert "production-ready" not in changelog.lower()
    assert "safe to deploy" not in changelog.lower()
    assert "production approved" not in changelog.lower()


def test_no_ci_cd_or_container_artefacts() -> None:
    for name in ("Dockerfile", "docker-compose.prod.yml", "Chart.yaml", "main.tf"):
        assert not (REPO_ROOT / name).exists()
    workflows = REPO_ROOT / ".github" / "workflows"
    if workflows.exists():
        for path in workflows.glob("*"):
            assert "ped" not in path.name.lower()

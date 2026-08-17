"""PED-I01 architecture boundaries for the runtime package."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.dbutil import REPO_ROOT

pytestmark = pytest.mark.ped_i01

RUNTIME_ROOT = REPO_ROOT / "src" / "aieos" / "platform" / "runtime"
BOUNDARY_DOC = REPO_ROOT / "docs" / "PED-I01-PRODUCTION-RUNTIME-CONFIG-CONTRACT.md"
ENV_EXAMPLE = REPO_ROOT / ".env.example"
MIGRATIONS = REPO_ROOT / "migrations" / "versions"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"

_FORBIDDEN_IMPORT_NEEDLES = (
    "tests.",
    "dotenv",
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

# PED-I06 authorizes uvicorn ONLY in platform.runtime.asgi.
_UVICORN_AUTHORIZED_RELATIVE = Path("asgi.py")


def _py_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*.py") if p.is_file()]


def test_runtime_db_driver_is_exact_psycopg3() -> None:
    from aieos.platform.runtime.config import REQUIRED_RUNTIME_DB_DRIVER

    assert REQUIRED_RUNTIME_DB_DRIVER == "postgresql+psycopg"
    config_src = (RUNTIME_ROOT / "config.py").read_text(encoding="utf-8")
    assert 'REQUIRED_RUNTIME_DB_DRIVER = "postgresql+psycopg"' in config_src
    assert 'frozenset({"postgresql"' not in config_src
    assert "postgresql+psycopg2" not in config_src
    doc = BOUNDARY_DOC.read_text(encoding="utf-8")
    assert "postgresql+psycopg://" in doc
    assert "Bare `postgresql://`" in doc or "bare `postgresql://`" in doc.lower()


def test_runtime_package_import_boundary() -> None:
    for path in _py_files(RUNTIME_ROOT):
        text = path.read_text(encoding="utf-8")
        lower = text.lower()
        for needle in _FORBIDDEN_IMPORT_NEEDLES:
            assert needle not in text, f"{path}: {needle}"
            assert f"import {needle}" not in lower
        # PED-I06: uvicorn allowed only in asgi.py; still forbidden elsewhere.
        if path.name != _UVICORN_AUTHORIZED_RELATIVE.name:
            assert "uvicorn" not in text, f"{path}: uvicorn only allowed in asgi.py"
        assert "StubSecurityContextResolver" not in text
        assert "AllowReviewAuthorization" not in text
        assert "AllowPublicationAuthorization" not in text
        assert "metadata.create_all" not in text
        if path.name != "database.py":
            assert "create_engine" not in text
        # No module-level production singleton (assignment at column 0)
        for line in text.splitlines():
            if line.startswith("app = compose_api_application") or line.startswith(
                "app = create_app"
            ):
                raise AssertionError(f"module-level app singleton in {path}: {line}")


def test_uvicorn_confined_to_authorized_asgi_module() -> None:
    asgi = RUNTIME_ROOT / "asgi.py"
    assert asgi.is_file()
    text = asgi.read_text(encoding="utf-8")
    assert "import uvicorn" in text
    assert "gunicorn" not in text
    assert "hypercorn" not in text
    for path in _py_files(RUNTIME_ROOT):
        if path == asgi:
            continue
        other = path.read_text(encoding="utf-8")
        assert "uvicorn" not in other, f"{path} must not import uvicorn"

def test_no_mutation_activation_in_runtime_package() -> None:
    for path in _py_files(RUNTIME_ROOT):
        text = path.read_text(encoding="utf-8")
        # Historical alias forbidden; PED-I03 uses AIEOS_API_MUTATION_ACTIVATION.
        assert "AIEOS_MUTATIONS_ENABLED" not in text
        if path.name != "activation.py":
            assert "MUTATIONS_ENABLED" not in text


def test_no_ci_cd_or_container_artefacts_added() -> None:
    # PED-I01 forbade root Dockerfile / prod compose / Helm / Terraform.
    # PED-I06 adds only a governed NON_PRODUCTION probe under deploy/oci/.
    for name in ("Dockerfile", "docker-compose.prod.yml", "Chart.yaml", "main.tf"):
        assert not (REPO_ROOT / name).exists()
    # No PED-I01 CI workflows introduced
    workflows = REPO_ROOT / ".github" / "workflows"
    if workflows.exists():
        for path in workflows.glob("*"):
            assert "ped" not in path.name.lower()


def test_migration_head_unchanged() -> None:
    versions = sorted(p.name for p in MIGRATIONS.glob("*.py") if p.name != "__init__.py")
    assert "pedi090001_security_authority.py" in versions
    assert "pedi10b2001_asset_authority_sor.py" in versions
    assert versions[-1] == "saii020001_security_audit_ledger.py"
    for path in MIGRATIONS.rglob("*.py"):
        body = path.read_text(encoding="utf-8")
        assert "pedi010001" not in body
        assert "pedi020001" not in body


def test_boundary_doc_and_changelog() -> None:
    doc = BOUNDARY_DOC.read_text(encoding="utf-8")
    assert "PED-I01" in doc
    assert "configuration/composition foundation" in doc.lower() or "configuration" in doc.lower()
    assert "NOT AUTHORIZED" in doc
    assert "production ready" not in doc.lower()
    assert "safe to deploy" not in doc.lower()
    assert "production-ready" not in doc.lower()
    assert "deployable" not in doc.lower()
    assert "ASGI" in doc
    assert "health" in doc.lower()
    assert "mutation" in doc.lower()
    changelog = CHANGELOG.read_text(encoding="utf-8")
    assert "PED-I01" in changelog
    assert "production-readiness foundation" in changelog.lower()
    assert "production-ready" not in changelog.lower()
    assert "deployable" not in changelog.lower()
    assert "production approved" not in changelog.lower()


def test_env_example_remains_local_warning() -> None:
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert "production" in text.lower()
    assert "Never point this at production" in text or "local/ephemeral" in text.lower()
    assert "SUPER_SECRET" not in text
    assert "AIEOS_CURSOR_SIGNING_KEY_B64" not in text

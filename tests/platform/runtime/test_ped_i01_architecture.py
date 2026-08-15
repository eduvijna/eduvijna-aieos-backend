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


def test_runtime_package_import_boundary() -> None:
    for path in _py_files(RUNTIME_ROOT):
        text = path.read_text(encoding="utf-8")
        lower = text.lower()
        for needle in _FORBIDDEN_IMPORT_NEEDLES:
            assert needle not in text, f"{path}: {needle}"
            assert f"import {needle}" not in lower
        assert "StubSecurityContextResolver" not in text
        assert "AllowReviewAuthorization" not in text
        assert "AllowPublicationAuthorization" not in text
        assert "create_engine" not in text
        assert "metadata.create_all" not in text
        # No module-level production singleton (assignment at column 0)
        for line in text.splitlines():
            if line.startswith("app = compose_api_application") or line.startswith(
                "app = create_app"
            ):
                raise AssertionError(f"module-level app singleton in {path}: {line}")


def test_no_health_or_mutation_activation() -> None:
    for path in _py_files(RUNTIME_ROOT):
        text = path.read_text(encoding="utf-8")
        assert "/livez" not in text
        assert "/readyz" not in text
        assert "MUTATIONS_ENABLED" not in text
        assert "AIEOS_MUTATIONS_ENABLED" not in text


def test_no_ci_cd_or_container_artefacts_added() -> None:
    for name in ("Dockerfile", "docker-compose.prod.yml", "Chart.yaml", "main.tf"):
        assert not (REPO_ROOT / name).exists()
    # No PED-I01 CI workflows introduced
    workflows = REPO_ROOT / ".github" / "workflows"
    if workflows.exists():
        for path in workflows.glob("*"):
            assert "ped" not in path.name.lower()


def test_migration_head_unchanged() -> None:
    versions = sorted(p.name for p in MIGRATIONS.glob("*.py") if p.name != "__init__.py")
    assert versions[-1] == "saii020001_security_audit_ledger.py"
    for path in MIGRATIONS.rglob("*.py"):
        assert "pedi010001" not in path.read_text(encoding="utf-8")


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

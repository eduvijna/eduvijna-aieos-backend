"""PED-I06 OCI runtime probe architecture boundaries."""

from __future__ import annotations

import re

import pytest

from tests.dbutil import REPO_ROOT

pytestmark = pytest.mark.ped_i06

DOCKERFILE = REPO_ROOT / "deploy" / "oci" / "Dockerfile.api-runtime-probe"
DOCKERIGNORE = REPO_ROOT / ".dockerignore"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
BOUNDARY_DOC = REPO_ROOT / "docs" / "PED-I06-ASGI-OCI-RUNTIME-FOUNDATION.md"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"
PROBE_APP = REPO_ROOT / "tools" / "runtime" / "asgi_http_probe.py"
PROBE_SCRIPT = REPO_ROOT / "tools" / "runtime" / "run_oci_runtime_probe.sh"

_BASE_TAG = "0.12.4-trixie-slim"
_BASE_DIGEST = (
    "sha256:8d033111899301598e33bd321b85f33f86e3ba2953ce00ff70a9cac020246a7c"
)
_FORBIDDEN_SECRET_NEEDLES = (
    "AIEOS_RUNTIME_DATABASE_URL",
    "AIEOS_CURSOR_SIGNING_KEY",
    "PASSWORD",
    "SECRET",
    "API_KEY",
    "TOKEN=",
    "postgresql+psycopg://",
)
_FORBIDDEN_MUTATION = "AIEOS_API_MUTATION_ACTIVATION"
_FORBIDDEN_CMD = (
    "compose_api_application",
    "create_app",
    "alembic upgrade",
    "alembic downgrade",
    "metadata.create_all",
)


def test_dockerfile_exists_at_governed_path() -> None:
    assert DOCKERFILE.is_file()
    assert not (REPO_ROOT / "Dockerfile").exists()
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "NON_PRODUCTION" in text
    assert 'io.eduvijna.aieos.classification="NON_PRODUCTION_RUNTIME_PROBE"' in text
    assert "production_authorized=true" not in text.lower()
    assert "deployable=true" not in text.lower()
    assert "mutation_authorized=true" not in text.lower()


def test_dockerfile_from_is_digest_pinned_uv_base() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    from_lines = [
        line for line in text.splitlines() if line.strip().upper().startswith("FROM ")
    ]
    assert len(from_lines) == 1
    frm = from_lines[0]
    assert f"ghcr.io/astral-sh/uv:{_BASE_TAG}@{_BASE_DIGEST}" in frm
    assert "@sha256:" in frm
    assert re.search(r"@sha256:[0-9a-f]{64}\b", frm)


def test_dockerfile_python_lock_nonroot_and_probe_cmd() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "uv python install 3.14.7" in text
    assert "uv sync --locked --no-dev --no-editable" in text
    assert "UV_PYTHON_INSTALL_DIR=/opt/python" in text
    assert "UV_PROJECT_ENVIRONMENT=/opt/venv" in text
    assert "useradd" in text and "10001" in text
    assert "groupadd" in text and "10001" in text
    assert re.search(r"(?m)^USER aieos\s*$", text)
    assert 'CMD ["uvicorn", "--version"]' in text
    for needle in _FORBIDDEN_CMD:
        assert needle not in text
    assert _FORBIDDEN_MUTATION not in text
    for needle in _FORBIDDEN_SECRET_NEEDLES:
        assert needle not in text


def test_dockerignore_excludes_sensitive_and_dev_artefacts() -> None:
    assert DOCKERIGNORE.is_file()
    text = DOCKERIGNORE.read_text(encoding="utf-8")
    for needle in (
        ".git",
        ".venv",
        ".env",
        ".env.*",
        "__pycache__",
        ".pytest_cache",
        "build/",
        "dist/",
        ".coverage",
        "htmlcov/",
    ):
        assert needle in text
    # Must not exclude required runtime sources
    assert "src/" not in text.splitlines() or not any(
        line.strip() == "src/" or line.strip() == "src" for line in text.splitlines()
    )


def test_ci_oci_runtime_probe_job() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert re.search(r"(?m)^\s+oci-runtime-probe:\s*$", text)
    assert "run_oci_runtime_probe.sh" in text
    assert "contents: read" in text
    for key in (
        "packages: write",
        "id-token: write",
        "deployments: write",
        "contents: write",
    ):
        assert key not in text
    assert "docker push" not in text.lower()
    assert "ghcr.io/" not in text or "Dockerfile.api-runtime-probe" in (
        DOCKERFILE.read_text(encoding="utf-8")
    )
    # Workflow itself must not push or publish
    lower = text.lower()
    assert "docker push" not in lower
    assert "softprops/action-gh-release" not in lower
    assert "aieos_api_mutation_activation=enabled" not in lower


def test_probe_app_is_test_only_and_tiny() -> None:
    assert PROBE_APP.is_file()
    assert PROBE_SCRIPT.is_file()
    text = PROBE_APP.read_text(encoding="utf-8")
    assert "NOT the product application" in text or "Test-only" in text
    assert "/livez" in text
    assert "compose_api_application" not in text
    assert "create_app" not in text
    assert "tests." not in text
    script = PROBE_SCRIPT.read_text(encoding="utf-8")
    assert "--read-only" in script
    assert "--cap-drop=ALL" in script
    assert "no-new-privileges" in script
    assert "docker push" not in script.lower()
    assert _FORBIDDEN_MUTATION not in script


def test_boundary_doc_and_changelog() -> None:
    assert BOUNDARY_DOC.is_file()
    doc = BOUNDARY_DOC.read_text(encoding="utf-8")
    assert "NON_PRODUCTION" in doc
    assert "uvicorn" in doc.lower()
    assert "0.51" in doc
    assert "3.14.7" in doc
    assert "proxy" in doc.lower()
    assert "NOT AUTHORIZED" in doc
    assert "production-ready" not in doc.lower()
    assert "safe to deploy" not in doc.lower()
    assert "production approved" not in doc.lower()
    changelog = CHANGELOG.read_text(encoding="utf-8")
    assert "PED-I06 ASGI server and NON_PRODUCTION OCI runtime viability foundation" in changelog
    assert "production-ready" not in changelog.lower()
    assert "safe to deploy" not in changelog.lower()
    assert "production approved" not in changelog.lower()

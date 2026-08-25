"""WPI-OCI-I01 production OCI architecture boundaries (static/adversarial)."""

from __future__ import annotations

import re

from tests.dbutil import REPO_ROOT

DOCKERFILE = REPO_ROOT / "deploy" / "oci" / "Dockerfile.backend-runtime"
PROBE = REPO_ROOT / "deploy" / "oci" / "Dockerfile.api-runtime-probe"
DOCKERIGNORE = REPO_ROOT / ".dockerignore"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
VALIDATION = REPO_ROOT / "tools" / "runtime" / "run_backend_oci_validation.sh"
DOC = REPO_ROOT / "docs" / "WPI-OCI-I01-BACKEND-PRODUCTION-OCI.md"

_BASE_TAG = "0.12.4-trixie-slim"
_BASE_DIGEST = (
    "sha256:8d033111899301598e33bd321b85f33f86e3ba2953ce00ff70a9cac020246a7c"
)


def test_production_dockerfile_exists_at_frozen_path() -> None:
    assert DOCKERFILE.is_file()
    assert PROBE.is_file()
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "PRODUCTION_BACKEND_RUNTIME" in text
    assert "Dockerfile.api-runtime-probe" in text or "NOT promote" in text or "not promote" in text.lower() or "Does NOT promote" in text


def test_runtime_probe_untouched_and_not_promoted() -> None:
    probe = PROBE.read_text(encoding="utf-8")
    assert "NON_PRODUCTION_RUNTIME_PROBE" in probe
    assert 'CMD ["uvicorn", "--version"]' in probe
    prod = DOCKERFILE.read_text(encoding="utf-8")
    assert "NON_PRODUCTION_RUNTIME_PROBE" not in prod
    assert "uvicorn" not in prod


def test_dockerfile_from_digest_pinned_uv_base() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    from_lines = [
        line for line in text.splitlines() if line.strip().upper().startswith("FROM ")
    ]
    assert len(from_lines) == 1
    frm = from_lines[0]
    assert f"ghcr.io/astral-sh/uv:{_BASE_TAG}@{_BASE_DIGEST}" in frm
    assert re.search(r"@sha256:[0-9a-f]{64}\b", frm)
    assert "latest" not in frm


def test_dockerfile_python_uv_lock_nonroot_labels_failclosed() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "uv python install 3.14.7" in text
    assert "uv sync --locked --no-dev --no-editable" in text
    assert "uv sync --locked" in text
    assert "--group dev" not in text
    assert "uv lock" not in text.split("uv sync", 1)[0]  # no unlock path in build
    assert "10001" in text
    assert re.search(r"(?m)^USER 10001:10001\s*$", text)
    assert "org.opencontainers.image.source=" in text
    assert "https://github.com/eduvijna/eduvijna-aieos-backend" in text
    assert "org.opencontainers.image.revision=" in text
    assert "${AIEOS_GIT_REVISION}" in text
    assert "AIEOS_BACKEND_RUNTIME_COMMAND_REQUIRED" in text
    assert "SystemExit(64)" in text or "exit(64)" in text
    assert "EXPOSE" not in text
    assert "registry.digitalocean.com" not in text
    assert "docker login" not in text
    assert "COPY .git" not in text
    assert "COPY tests" not in text
    assert "workflow_dispatcher_main" not in text.split("CMD", 1)[-1]
    assert "temporal_worker_main" not in text.split("CMD", 1)[-1]
    assert "api_main" not in text
    assert "event_dispatcher_main" not in text


def test_dockerignore_continues_excluding_sensitive_artefacts() -> None:
    text = DOCKERIGNORE.read_text(encoding="utf-8")
    for needle in (".git", ".env", ".env.*", ".secrets/", "*.pem", "*.key", ".credentials/"):
        assert needle in text


def test_ci_job_backend_production_oci_has_no_registry_credentials() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "backend-production-oci:" in text
    assert "name: backend-production-oci" in text
    assert "run_backend_oci_validation.sh" in text
    assert "b153193256450d0ce8afe0b5d2127dfbfd8f2123" in text
    assert "205e15bda0047b42aef1c4f67bb09fe4f156440a" in text
    assert "docker login" not in text
    assert "docker push" not in text
    assert "DIGITALOCEAN_TOKEN" not in text
    assert "registry.digitalocean.com" not in text
    # New job must not declare a secrets: mapping
    block = text.split("backend-production-oci:", 1)[1].split("\n  verified-build:", 1)[0]
    assert "secrets:" not in block


def test_validation_script_forbids_publication_paths() -> None:
    assert VALIDATION.is_file()
    text = VALIDATION.read_text(encoding="utf-8")
    # Executable publication paths must not appear as commands.
    assert not re.search(r"(?m)^\s*docker\s+login\b", text)
    assert not re.search(r"(?m)^\s*docker\s+push\b", text)
    assert "doctl" not in text
    assert "registry.digitalocean.com" not in text
    assert "linux/amd64" in text
    assert "10001" in text


def test_docs_exist_and_state_non_authorization() -> None:
    assert DOC.is_file()
    text = DOC.read_text(encoding="utf-8")
    assert "ADR-AIEOS-051" in text
    assert "NOT AUTHORIZED" in text
    assert "8f4dd172e6a0ba8b4ad944b0ae22060442356342" in text
    assert "WPI-OCI-I01 implementation BASE" in text or "implementation BASE" in text
    assert "PAUSED" in text

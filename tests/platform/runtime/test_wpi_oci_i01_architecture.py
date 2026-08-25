"""WPI-OCI-I01 / I01R1 production OCI architecture boundaries (static/adversarial)."""

from __future__ import annotations

import re

from tests.dbutil import REPO_ROOT

DOCKERFILE = REPO_ROOT / "deploy" / "oci" / "Dockerfile.backend-runtime"
PROBE = REPO_ROOT / "deploy" / "oci" / "Dockerfile.api-runtime-probe"
DOCKERIGNORE = REPO_ROOT / ".dockerignore"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
VALIDATION = REPO_ROOT / "tools" / "runtime" / "run_backend_oci_validation.sh"
DOC = REPO_ROOT / "docs" / "WPI-OCI-I01-BACKEND-PRODUCTION-OCI.md"
BUILD_TOOL = REPO_ROOT / "tools" / "release" / "build_backend_oci_provenance.py"

_BASE_TAG = "0.12.4-trixie-slim"
_BASE_DIGEST = (
    "sha256:8d033111899301598e33bd321b85f33f86e3ba2953ce00ff70a9cac020246a7c"
)
_ARCH_PIN = "b153193256450d0ce8afe0b5d2127dfbfd8f2123"
_INFRA_PIN = "205e15bda0047b42aef1c4f67bb09fe4f156440a"


def _oci_job_block() -> str:
    text = WORKFLOW.read_text(encoding="utf-8")
    return text.split("backend-production-oci:", 1)[1].split("\n  verified-build:", 1)[0]


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
    assert "uv lock" not in text.split("uv sync", 1)[0]
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


def test_dockerfile_build_args_fail_closed_no_unknown_defaults() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "AIEOS_GIT_REVISION=unknown" not in text
    assert "AIEOS_ARCHITECTURE_REVISION=unknown" not in text
    assert "AIEOS_INFRASTRUCTURE_REVISION=unknown" not in text
    assert re.search(r"(?m)^ARG AIEOS_GIT_REVISION\s*$", text)
    assert re.search(r"(?m)^ARG AIEOS_ARCHITECTURE_REVISION\s*$", text)
    assert re.search(r"(?m)^ARG AIEOS_INFRASTRUCTURE_REVISION\s*$", text)
    assert "^[0-9a-f]{40}$" in text
    assert 'AIEOS_APPLICATION_VERSION" = "$(tr -d' in text or "AIEOS_APPLICATION_VERSION}" in text
    assert "VERSION" in text


def test_dockerignore_continues_excluding_sensitive_artefacts() -> None:
    text = DOCKERIGNORE.read_text(encoding="utf-8")
    for needle in (".git", ".env", ".env.*", ".secrets/", "*.pem", "*.key", ".credentials/"):
        assert needle in text


def test_ci_job_backend_production_oci_has_no_registry_credentials() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "backend-production-oci:" in text
    assert "name: backend-production-oci" in text
    assert "run_backend_oci_validation.sh" in text
    assert _ARCH_PIN in text
    assert _INFRA_PIN in text
    assert "docker login" not in text
    assert "docker push" not in text
    assert "DIGITALOCEAN_TOKEN" not in text
    assert "registry.digitalocean.com" not in text
    block = _oci_job_block()
    assert "secrets:" not in block


def test_ci_uses_exact_pr_head_not_synthetic_merge_sha() -> None:
    block = _oci_job_block()
    assert "github.event.pull_request.head.sha" in block
    assert "steps.source.outputs.sha" in block
    assert "ref: ${{ steps.source.outputs.sha }}" in block
    assert "AIEOS_BACKEND_GIT_SHA: ${{ steps.source.outputs.sha }}" in block
    # Must not use github.sha as Backend provenance authority on pull_request.
    assert "AIEOS_BACKEND_GIT_SHA: ${{ github.sha }}" not in block
    assert 'test "${SOURCE_SHA}" != "${GITHUB_SHA}"' in block
    assert f'AIEOS_ARCHITECTURE_GIT_SHA: {_ARCH_PIN}' in WORKFLOW.read_text(encoding="utf-8")
    assert f'test "${{AIEOS_ARCHITECTURE_GIT_SHA}}" = "{_ARCH_PIN}"' in block
    assert f'test "${{AIEOS_INFRASTRUCTURE_GIT_SHA}}" = "{_INFRA_PIN}"' in block
    assert (
        'test "b153193256450d0ce8afe0b5d2127dfbfd8f2123" = "b153193256450d0ce8afe0b5d2127dfbfd8f2123"'
        not in block
    )

def test_validation_script_forbids_publication_paths() -> None:
    assert VALIDATION.is_file()
    text = VALIDATION.read_text(encoding="utf-8")
    assert not re.search(r"(?m)^\s*docker\s+login\b", text)
    assert not re.search(r"(?m)^\s*docker\s+push\b", text)
    assert "doctl" not in text
    assert "registry.digitalocean.com" not in text
    assert "linux/amd64" in text
    assert "10001" in text
    assert "AIEOS_BACKEND_GIT_SHA:?AIEOS_BACKEND_GIT_SHA required" in text
    assert "env-config-ok" in text
    assert "forbidden config material" in text


def test_validation_script_auth_scan_is_fail_closed() -> None:
    text = VALIDATION.read_text(encoding="utf-8")
    # Must not use a grep pipeline that merely filters matching lines as "success".
    assert "grep -viE 'auths|dockercfg" not in text
    assert 'fail(f"forbidden config material:' in text or "forbidden config material" in text
    assert "dop_v1_" in text
    assert "dockercfg" in text


def test_no_allow_dirty_cli_bypass() -> None:
    text = BUILD_TOOL.read_text(encoding="utf-8")
    assert "--allow-dirty" not in text or 'raise SystemExit("--allow-dirty is not authorized")' in text
    assert 'add_argument(\n        "--allow-dirty"' not in text
    assert 'add_argument("--allow-dirty"' not in text


def test_docs_exist_and_state_non_authorization() -> None:
    assert DOC.is_file()
    text = DOC.read_text(encoding="utf-8")
    assert "ADR-AIEOS-051" in text
    assert "NOT AUTHORIZED" in text
    assert "8f4dd172e6a0ba8b4ad944b0ae22060442356342" in text
    assert "WPI-OCI-I01 implementation BASE" in text or "implementation BASE" in text
    assert "PAUSED" in text
    assert "exact PR HEAD" in text or "PR head" in text or "pull_request.head.sha" in text

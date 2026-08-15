"""PED-I04 architecture boundaries for CI and verified builds."""

from __future__ import annotations

import re

import pytest

from tests.dbutil import REPO_ROOT

pytestmark = pytest.mark.ped_i04

WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
BOUNDARY_DOC = REPO_ROOT / "docs" / "PED-I04-CI-VERIFIED-BUILD-CONTRACT.md"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"
TOOLS = REPO_ROOT / "tools" / "release"

_ACTION_SHA = {
    "actions/checkout": "3d3c42e5aac5ba805825da76410c181273ba90b1",
    "astral-sh/setup-uv": "20cfd1bf945f4377ade1205e4dbc17946fc9a30d",
    "actions/upload-artifact": "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
}

_FORBIDDEN_DEPLOY = (
    "kubectl",
    "helm",
    "terraform",
    "docker push",
    "aws ",
    "gcloud ",
    "az ",
    "ssh ",
)


def test_ci_workflow_exists_and_triggers() -> None:
    assert WORKFLOW.is_file()
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "pull_request_target" not in text
    assert re.search(r"(?m)^on:\s*$", text)
    assert "pull_request:" in text
    assert "push:" in text
    assert "- main" in text
    assert "workflow_dispatch" not in text


def test_workflow_permissions_and_action_pins() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "permissions:" in text
    assert "contents: read" in text
    for key in (
        "packages: write",
        "id-token: write",
        "deployments: write",
        "actions: write",
        "security-events: write",
        "contents: write",
    ):
        assert key not in text
    assert "persist-credentials: false" in text
    for uses_line in [line for line in text.splitlines() if "uses:" in line]:
        assert "@" in uses_line
        ref = uses_line.split("@", 1)[1].split("#", 1)[0].strip()
        assert len(ref) == 40 and all(c in "0123456789abcdef" for c in ref)
    for action, sha in _ACTION_SHA.items():
        assert f"{action}@{sha}" in text
    assert 'version: "0.12.4"' in text
    assert "3.14.7" in text


def test_quality_and_verified_build_jobs() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert re.search(r"(?m)^\s+quality-gate:\s*$", text)
    assert re.search(r"(?m)^\s+verified-build:\s*$", text)
    assert "needs:" in text
    assert "- quality-gate" in text
    assert "github.event_name == 'push'" in text
    assert "refs/heads/main" in text
    assert "uv lock --check" in text
    assert "uv sync --locked --group dev" in text
    assert "compileall" in text
    assert "pytest -v" in text
    assert "uv build" in text
    assert "build_verified_bundle.py" in text
    assert "verify_verified_bundle.py" in text
    assert "aieos-verified-build-" in text
    # No ambiguous latest/production/stable artifact names
    assert "name: latest" not in text
    assert "name: production" not in text
    assert "name: stable" not in text


def test_no_deploy_registry_mutation_or_latest() -> None:
    """PED-I04 prohibitions retained; ASGI/OCI probe portions superseded by PED-I06.

    Advancement (PED-I06):
    - uvicorn may appear as a direct runtime dependency
    - a governed NON_PRODUCTION Dockerfile may exist under deploy/oci/
    - CI may build/smoke that local probe image

    Still forbidden (PED-I04 retained):
    - docker push / registry publication
    - cloud deploy tooling
    - production artifact naming (latest/production/stable)
    - mutation activation enablement
    - GitHub Release publication
    - root-level production Dockerfile
    """
    text = WORKFLOW.read_text(encoding="utf-8").lower()
    for needle in _FORBIDDEN_DEPLOY:
        assert needle not in text
    assert "ghcr.io" not in text  # no registry login/publish in workflow
    assert "pypi" not in text
    assert "softprops/action-gh-release" not in text
    assert "aieos_api_mutation_activation=enabled" not in text
    assert not (REPO_ROOT / "Dockerfile").exists()
    probe = REPO_ROOT / "deploy" / "oci" / "Dockerfile.api-runtime-probe"
    assert probe.is_file()
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "uvicorn>=" in pyproject
    for server in ("gunicorn", "hypercorn"):
        assert server not in pyproject


def test_tools_and_docs() -> None:
    assert (TOOLS / "build_verified_bundle.py").is_file()
    assert (TOOLS / "verify_verified_bundle.py").is_file()
    assert (TOOLS / "common.py").is_file()
    doc = BOUNDARY_DOC.read_text(encoding="utf-8")
    assert "quality-gate" in doc
    assert "NOT AUTHORIZED" in doc
    assert "branch protection" in doc.lower()
    assert "does not claim" in doc.lower() or "not already" in doc.lower()
    assert "production-ready" not in doc.lower()
    assert "safe to deploy" not in doc.lower()
    changelog = CHANGELOG.read_text(encoding="utf-8")
    assert "PED-I04" in changelog
    assert "CI quality gates and immutable verified build bundle foundation" in changelog
    assert "production-ready" not in changelog.lower()
    assert "safe to deploy" not in changelog.lower()
    assert "production approved" not in changelog.lower()


def test_no_src_aieos_mutation_activation_change() -> None:
    activation = (
        REPO_ROOT / "src" / "aieos" / "platform" / "runtime" / "activation.py"
    ).read_text(encoding="utf-8")
    assert "AIEOS_API_MUTATION_ACTIVATION" in activation
    assert "ENABLED" in activation

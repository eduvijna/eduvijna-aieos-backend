"""WPI-OCI-I01 Backend production OCI provenance helpers (stdlib only).

Separate from PED-I04 NON_PRODUCTION verified-bundle constants in common.py.
Do not reuse or mutate those constants.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
ARTIFACT_KIND = "AIEOS_BACKEND_PRODUCTION_OCI_PROVENANCE"
CLASSIFICATION = "PRODUCTION_RUNTIME_CANDIDATE"
SOURCE_REPOSITORY = "eduvijna/eduvijna-aieos-backend"
EXPECTED_PYTHON_VERSION = "3.14.7"
EXPECTED_UV_VERSION = "0.12.4"
EXPECTED_BUILD_PLATFORM = "linux/amd64"
EXPECTED_RUNTIME_USER = "10001:10001"
EXPECTED_IMAGE_SOURCE = "https://github.com/eduvijna/eduvijna-aieos-backend"
EXPECTED_CLASSIFICATION_LABEL = "PRODUCTION_BACKEND_RUNTIME"
FAIL_CLOSED_MARKER = "AIEOS_BACKEND_RUNTIME_COMMAND_REQUIRED"
FAIL_CLOSED_EXIT_CODE = 64

BASE_IMAGE_REFERENCE = (
    "ghcr.io/astral-sh/uv:0.12.4-trixie-slim@"
    "sha256:8d033111899301598e33bd321b85f33f86e3ba2953ce00ff70a9cac020246a7c"
)
BASE_IMAGE_DIGEST = (
    "sha256:8d033111899301598e33bd321b85f33f86e3ba2953ce00ff70a9cac020246a7c"
)

DOCKERFILE_REL = Path("deploy/oci/Dockerfile.backend-runtime")

_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_PYPROJECT_VERSION = re.compile(r'(?m)^version\s*=\s*"([^"]+)"')

REQUIRED_OCI_LABELS = (
    "org.opencontainers.image.title",
    "org.opencontainers.image.description",
    "org.opencontainers.image.version",
    "org.opencontainers.image.source",
    "org.opencontainers.image.revision",
    "io.eduvijna.aieos.classification",
    "io.eduvijna.aieos.application_version",
    "io.eduvijna.aieos.git_revision",
    "io.eduvijna.aieos.architecture_revision",
    "io.eduvijna.aieos.infrastructure_revision",
)

REQUIRED_RECEIPT_FIELDS = frozenset(
    {
        "schema_version",
        "artifact_kind",
        "classification",
        "source_repository",
        "application_version",
        "backend_git_sha",
        "architecture_git_sha",
        "infrastructure_git_sha",
        "python_version",
        "uv_version",
        "build_platform",
        "dockerfile_sha256",
        "uv_lock_sha256",
        "base_image",
        "image_config_id",
        "oci_labels",
        "runtime_user",
        "default_command",
        "source_clean",
        "validation_status",
        "publication_performed",
        "publication_authorized",
        "deployment_authorized",
    }
)

FORBIDDEN_RECEIPT_FIELDS = frozenset(
    {
        "registry",
        "repository",
        "manifest_digest",
        "provider_request_id",
        "publication_classification",
        "publication_timestamp",
        "published_at",
        "source_sha_tag",
        "pat",
        "token",
        "authorization",
        "password",
        "credential",
        "secret",
        "docker_auth",
        "auths",
    }
)

_FORBIDDEN_VALUE_NEEDLES = (
    "dop_v1_",
    "EV[",
    "Authorization:",
    "BEGIN PRIVATE KEY",
)


def require_full_git_sha(value: str, *, label: str = "git_sha") -> str:
    if not isinstance(value, str) or not _GIT_SHA.fullmatch(value):
        raise ValueError(f"{label} must be an exact 40-character lowercase hexadecimal SHA")
    return value


def require_sha256_hex(value: str, *, label: str) -> str:
    if not isinstance(value, str) or not _SHA256_HEX.fullmatch(value):
        raise ValueError(f"{label} must be 64-character lowercase hex SHA-256")
    return value


def require_digest(value: str, *, label: str) -> str:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise ValueError(f"{label} must be sha256:<64 lowercase hex>")
    return value


def sha256_file_hex(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def read_version_file(repo_root: Path) -> str:
    return (repo_root / "VERSION").read_text(encoding="utf-8").strip()


def read_pyproject_version(repo_root: Path) -> str:
    text = (repo_root / "pyproject.toml").read_text(encoding="utf-8")
    match = _PYPROJECT_VERSION.search(text)
    if match is None:
        raise ValueError("project.version not found in pyproject.toml")
    return match.group(1)


def assert_version_coherence(repo_root: Path) -> str:
    version = read_version_file(repo_root)
    pyproject = read_pyproject_version(repo_root)
    if version != pyproject:
        raise ValueError(f"VERSION ({version}) != pyproject.toml version ({pyproject})")
    return version


def extract_base_image_digest(reference: str) -> str:
    if "@sha256:" not in reference:
        raise ValueError("base image reference must be digest-pinned (@sha256:...)")
    digest = reference.split("@", 1)[1]
    return require_digest(digest, label="base_image.digest")


def validate_base_image_reference(reference: str) -> str:
    if not isinstance(reference, str) or not reference:
        raise ValueError("base_image.reference required")
    if "latest" in reference.split(":")[-1] and "@" not in reference:
        raise ValueError("mutable/non-digest base image rejected")
    digest = extract_base_image_digest(reference)
    if digest != BASE_IMAGE_DIGEST:
        # Allow only the frozen governed base for I01 receipts.
        raise ValueError("base image digest does not match governed uv 0.12.4 pin")
    return reference


def reject_secret_like_values(obj: Any, *, path: str = "$") -> None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            key_l = str(key).lower()
            if key_l in FORBIDDEN_RECEIPT_FIELDS:
                raise ValueError(f"forbidden receipt field path={path}.{key}")
            reject_secret_like_values(value, path=f"{path}.{key}")
        return
    if isinstance(obj, list):
        for idx, value in enumerate(obj):
            reject_secret_like_values(value, path=f"{path}[{idx}]")
        return
    if isinstance(obj, str):
        for needle in _FORBIDDEN_VALUE_NEEDLES:
            if needle in obj:
                raise ValueError(f"forbidden secret-like value at {path}")


def default_command_contract() -> list[str]:
    return [
        "python",
        "-c",
        "import sys; print('AIEOS_BACKEND_RUNTIME_COMMAND_REQUIRED', file=sys.stderr); raise SystemExit(64)",
    ]


def assert_default_command(cmd: Any) -> list[str]:
    if not isinstance(cmd, list) or not all(isinstance(x, str) for x in cmd):
        raise ValueError("default_command must be a list of strings")
    expected = default_command_contract()
    if cmd != expected:
        # Allow equivalent marker + exit 64 contract without exact string identity
        joined = " ".join(cmd)
        if FAIL_CLOSED_MARKER not in joined:
            raise ValueError("default_command missing fail-closed marker")
        if "64" not in joined and "SystemExit(64)" not in joined:
            raise ValueError("default_command must exit 64")
        if any(
            token in joined
            for token in (
                "workflow_dispatcher_main",
                "temporal_worker_main",
                "api_main",
                "event_dispatcher_main",
                "uvicorn",
            )
        ):
            raise ValueError("default_command must not start a production workload")
        return list(cmd)
    return list(cmd)

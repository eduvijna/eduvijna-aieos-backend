"""Build sanitized pre-publication Backend OCI provenance receipt (WPI-OCI-I01)."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from backend_oci_common import (
    ARTIFACT_KIND,
    BASE_IMAGE_DIGEST,
    BASE_IMAGE_REFERENCE,
    CLASSIFICATION,
    DOCKERFILE_REL,
    EXPECTED_BUILD_PLATFORM,
    EXPECTED_CLASSIFICATION_LABEL,
    EXPECTED_IMAGE_SOURCE,
    EXPECTED_PYTHON_VERSION,
    EXPECTED_RUNTIME_USER,
    EXPECTED_UV_VERSION,
    REQUIRED_OCI_LABELS,
    SCHEMA_VERSION,
    SOURCE_REPOSITORY,
    assert_default_command,
    assert_version_coherence,
    canonical_json,
    reject_secret_like_values,
    require_full_git_sha,
    sha256_file_hex,
    validate_base_image_reference,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _run(cmd: list[str], *, cwd: Path) -> str:
    completed = subprocess.run(
        cmd,
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def assert_clean_source(repo_root: Path, backend_git_sha: str) -> None:
    head = _run(["git", "rev-parse", "HEAD"], cwd=repo_root)
    if head != backend_git_sha:
        raise ValueError(f"HEAD ({head}) != backend-git-sha ({backend_git_sha})")
    porcelain = _run(["git", "status", "--porcelain"], cwd=repo_root)
    if porcelain:
        raise ValueError(
            "dirty source rejected in authoritative mode:\n" + porcelain
        )


def _inspect_image(image: str) -> dict[str, Any]:
    raw = _run(
        ["docker", "image", "inspect", image, "--format", "{{json .}}"],
        cwd=REPO_ROOT,
    )
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("docker image inspect returned non-object")
    return data


def _labels_from_inspect(inspect_obj: dict[str, Any]) -> dict[str, str]:
    config = inspect_obj.get("Config")
    if not isinstance(config, dict):
        raise ValueError("missing Config")
    labels = config.get("Labels") or {}
    if not isinstance(labels, dict):
        raise ValueError("Labels must be object")
    out: dict[str, str] = {}
    for key in REQUIRED_OCI_LABELS:
        val = labels.get(key)
        if not isinstance(val, str) or not val:
            raise ValueError(f"missing required OCI label: {key}")
        out[key] = val
    return out


def _runtime_user(inspect_obj: dict[str, Any]) -> str:
    config = inspect_obj.get("Config")
    if not isinstance(config, dict):
        raise ValueError("missing Config")
    user = config.get("User")
    if not isinstance(user, str) or not user:
        raise ValueError("Config.User missing")
    if user == "aieos":
        # Normalize name form if ever present; I01 Dockerfile uses numeric.
        return EXPECTED_RUNTIME_USER
    if user != EXPECTED_RUNTIME_USER:
        raise ValueError(f"runtime_user must be {EXPECTED_RUNTIME_USER}, got {user!r}")
    return user


def _default_command(inspect_obj: dict[str, Any]) -> list[str]:
    config = inspect_obj.get("Config")
    if not isinstance(config, dict):
        raise ValueError("missing Config")
    cmd = config.get("Cmd")
    return assert_default_command(cmd)


def _image_config_id(inspect_obj: dict[str, Any]) -> str:
    # Docker/local OCI CONFIG identity — NOT a registry manifest digest.
    cfg = inspect_obj.get("Config")
    # Prefer Image field when present; else Id is the local image id (config-linked).
    image_id = inspect_obj.get("Id")
    if not isinstance(image_id, str) or not image_id:
        raise ValueError("image Id missing")
    # Keep as opaque local config identity string (sha256:... allowed for local id).
    return image_id


def _os_arch(inspect_obj: dict[str, Any]) -> tuple[str, str]:
    os_name = inspect_obj.get("Os")
    arch = inspect_obj.get("Architecture")
    if os_name != "linux" or arch != "amd64":
        raise ValueError(f"expected linux/amd64, got {os_name}/{arch}")
    return str(os_name), str(arch)


def build_prepublication_receipt(
    *,
    repo_root: Path,
    image: str,
    backend_git_sha: str,
    architecture_git_sha: str,
    infrastructure_git_sha: str,
    inspect_obj: dict[str, Any] | None = None,
    require_clean_source: bool = True,
) -> dict[str, Any]:
    """Pure/testable receipt builder. When inspect_obj is provided, skips docker."""
    backend_git_sha = require_full_git_sha(backend_git_sha, label="backend_git_sha")
    architecture_git_sha = require_full_git_sha(
        architecture_git_sha, label="architecture_git_sha"
    )
    infrastructure_git_sha = require_full_git_sha(
        infrastructure_git_sha, label="infrastructure_git_sha"
    )
    if require_clean_source:
        assert_clean_source(repo_root, backend_git_sha)

    application_version = assert_version_coherence(repo_root)
    dockerfile = repo_root / DOCKERFILE_REL
    if not dockerfile.is_file():
        raise ValueError(f"missing {DOCKERFILE_REL}")
    dockerfile_sha256 = sha256_file_hex(dockerfile)
    uv_lock_sha256 = sha256_file_hex(repo_root / "uv.lock")

    if inspect_obj is None:
        inspect_obj = _inspect_image(image)
    _os_arch(inspect_obj)
    labels = _labels_from_inspect(inspect_obj)
    if labels["org.opencontainers.image.source"] != EXPECTED_IMAGE_SOURCE:
        raise ValueError("image source label mismatch")
    if labels["org.opencontainers.image.revision"] != backend_git_sha:
        raise ValueError("revision label must equal backend_git_sha")
    if labels["io.eduvijna.aieos.git_revision"] != backend_git_sha:
        raise ValueError("EduVijna git_revision must equal backend_git_sha")
    if labels["io.eduvijna.aieos.architecture_revision"] != architecture_git_sha:
        raise ValueError("architecture_revision label mismatch")
    if labels["io.eduvijna.aieos.infrastructure_revision"] != infrastructure_git_sha:
        raise ValueError("infrastructure_revision label mismatch")
    if labels["io.eduvijna.aieos.classification"] != EXPECTED_CLASSIFICATION_LABEL:
        raise ValueError("classification label mismatch")
    if labels["org.opencontainers.image.version"] != application_version:
        raise ValueError("image version label mismatch")
    if labels["io.eduvijna.aieos.application_version"] != application_version:
        raise ValueError("application_version label mismatch")

    validate_base_image_reference(BASE_IMAGE_REFERENCE)

    receipt: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "artifact_kind": ARTIFACT_KIND,
        "classification": CLASSIFICATION,
        "source_repository": SOURCE_REPOSITORY,
        "application_version": application_version,
        "backend_git_sha": backend_git_sha,
        "architecture_git_sha": architecture_git_sha,
        "infrastructure_git_sha": infrastructure_git_sha,
        "python_version": EXPECTED_PYTHON_VERSION,
        "uv_version": EXPECTED_UV_VERSION,
        "build_platform": EXPECTED_BUILD_PLATFORM,
        "dockerfile_sha256": dockerfile_sha256,
        "uv_lock_sha256": uv_lock_sha256,
        "base_image": {
            "reference": BASE_IMAGE_REFERENCE,
            "digest": BASE_IMAGE_DIGEST,
        },
        "image_config_id": _image_config_id(inspect_obj),
        "oci_labels": labels,
        "runtime_user": _runtime_user(inspect_obj),
        "default_command": _default_command(inspect_obj),
        "source_clean": True,
        "validation_status": "PASS",
        "publication_performed": False,
        "publication_authorized": False,
        "deployment_authorized": False,
    }
    reject_secret_like_values(receipt)
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build Backend OCI pre-publication provenance")
    parser.add_argument("--image", required=True)
    parser.add_argument("--backend-git-sha", required=True)
    parser.add_argument("--architecture-git-sha", required=True)
    parser.add_argument("--infrastructure-git-sha", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Development only; authoritative mode must omit this flag",
    )
    args = parser.parse_args(argv)

    # Ensure tools/release is importable when invoked as a script path.
    sys.path.insert(0, str(Path(__file__).resolve().parent))

    receipt = build_prepublication_receipt(
        repo_root=REPO_ROOT,
        image=args.image,
        backend_git_sha=args.backend_git_sha,
        architecture_git_sha=args.architecture_git_sha,
        infrastructure_git_sha=args.infrastructure_git_sha,
        require_clean_source=not args.allow_dirty,
    )
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    text = canonical_json(receipt) + "\n"
    out.write_text(text, encoding="utf-8", newline="\n")
    print(f"receipt_sha256={sha256_file_hex(out)}")
    print(f"wrote={out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

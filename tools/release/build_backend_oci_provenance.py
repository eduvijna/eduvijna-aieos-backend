"""Build sanitized pre-publication Backend OCI provenance receipt (WPI-OCI-I01R1E1)."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from backend_oci_common import (
    ARTIFACT_KIND,
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
    assert_clean_git_source,
    assert_default_command,
    assert_version_coherence,
    canonical_json,
    parse_dockerfile_base_image,
    parse_python_version_output,
    parse_uv_version_output,
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


def _inspect_image(image: str) -> dict[str, Any]:
    raw = _run(
        ["docker", "image", "inspect", image, "--format", "{{json .}}"],
        cwd=REPO_ROOT,
    )
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("docker image inspect returned non-object")
    return data


def _docker_run_stdout(image: str, *args: str) -> str:
    return _run(
        [
            "docker",
            "run",
            "--rm",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            image,
            *args,
        ],
        cwd=REPO_ROOT,
    )


def observe_python_version_from_image(image: str) -> str:
    return parse_python_version_output(_docker_run_stdout(image, "python", "--version"))


def observe_uv_version_from_image(image: str) -> str:
    return parse_uv_version_output(_docker_run_stdout(image, "uv", "--version"))


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
    if user != EXPECTED_RUNTIME_USER:
        raise ValueError(f"runtime_user must be exactly {EXPECTED_RUNTIME_USER}, got {user!r}")
    return user


def _default_command(inspect_obj: dict[str, Any]) -> list[str]:
    config = inspect_obj.get("Config")
    if not isinstance(config, dict):
        raise ValueError("missing Config")
    cmd = config.get("Cmd")
    return assert_default_command(cmd)


def _image_config_id(inspect_obj: dict[str, Any]) -> str:
    # Docker/local OCI CONFIG identity — NOT a registry manifest digest.
    image_id = inspect_obj.get("Id")
    if not isinstance(image_id, str) or not image_id:
        raise ValueError("image Id missing")
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
    observed_python_version: str | None = None,
    observed_uv_version: str | None = None,
) -> dict[str, Any]:
    """Authoritative pre-publication receipt builder.

    Always proves clean source identity before emitting source_clean=true /
    validation_status=PASS. No dirty-source bypass parameter exists.
    """
    backend_git_sha = require_full_git_sha(backend_git_sha, label="backend_git_sha")
    architecture_git_sha = require_full_git_sha(
        architecture_git_sha, label="architecture_git_sha"
    )
    infrastructure_git_sha = require_full_git_sha(
        infrastructure_git_sha, label="infrastructure_git_sha"
    )
    assert_clean_git_source(repo_root, backend_git_sha)

    application_version = assert_version_coherence(repo_root)
    dockerfile = repo_root / DOCKERFILE_REL
    if not dockerfile.is_file():
        raise ValueError(f"missing {DOCKERFILE_REL}")
    dockerfile_text = dockerfile.read_text(encoding="utf-8")
    base_reference, base_digest = parse_dockerfile_base_image(dockerfile_text)
    validate_base_image_reference(base_reference)
    dockerfile_sha256 = sha256_file_hex(dockerfile)
    uv_lock_sha256 = sha256_file_hex(repo_root / "uv.lock")

    if inspect_obj is None:
        inspect_obj = _inspect_image(image)
        observed_python_version = observe_python_version_from_image(image)
        observed_uv_version = observe_uv_version_from_image(image)
    else:
        if observed_python_version is None or observed_uv_version is None:
            raise ValueError(
                "observed_python_version and observed_uv_version required with inspect_obj"
            )

    os_name, arch = _os_arch(inspect_obj)
    build_platform = f"{os_name}/{arch}"
    if build_platform != EXPECTED_BUILD_PLATFORM:
        raise ValueError(f"build_platform mismatch: {build_platform}")

    if observed_python_version != EXPECTED_PYTHON_VERSION:
        raise ValueError(
            f"observed python_version {observed_python_version!r} != expected "
            f"{EXPECTED_PYTHON_VERSION!r}"
        )
    if observed_uv_version != EXPECTED_UV_VERSION:
        raise ValueError(
            f"observed uv_version {observed_uv_version!r} != expected "
            f"{EXPECTED_UV_VERSION!r}"
        )

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

    runtime_user = _runtime_user(inspect_obj)
    default_command = _default_command(inspect_obj)

    receipt: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "artifact_kind": ARTIFACT_KIND,
        "classification": CLASSIFICATION,
        "source_repository": SOURCE_REPOSITORY,
        "application_version": application_version,
        "backend_git_sha": backend_git_sha,
        "architecture_git_sha": architecture_git_sha,
        "infrastructure_git_sha": infrastructure_git_sha,
        "python_version": observed_python_version,
        "uv_version": observed_uv_version,
        "build_platform": build_platform,
        "dockerfile_sha256": dockerfile_sha256,
        "uv_lock_sha256": uv_lock_sha256,
        "base_image": {
            "reference": base_reference,
            "digest": base_digest,
        },
        "image_config_id": _image_config_id(inspect_obj),
        "oci_labels": labels,
        "runtime_user": runtime_user,
        "default_command": default_command,
        "source_clean": True,
        "validation_status": "PASS",
        "publication_performed": False,
        "publication_authorized": False,
        "deployment_authorized": False,
    }
    reject_secret_like_values(receipt)
    if receipt["validation_status"] != "PASS" or receipt["source_clean"] is not True:
        raise ValueError("authoritative receipt must be PASS with source_clean=true")
    return receipt


def main(argv: list[str] | None = None) -> int:
    argv_list = list(argv) if argv is not None else sys.argv[1:]
    if "--allow-dirty" in argv_list:
        raise SystemExit("--allow-dirty is not authorized")

    parser = argparse.ArgumentParser(description="Build Backend OCI pre-publication provenance")
    parser.add_argument("--image", required=True)
    parser.add_argument("--backend-git-sha", required=True)
    parser.add_argument("--architecture-git-sha", required=True)
    parser.add_argument("--infrastructure-git-sha", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv_list)

    sys.path.insert(0, str(Path(__file__).resolve().parent))

    receipt = build_prepublication_receipt(
        repo_root=REPO_ROOT,
        image=args.image,
        backend_git_sha=args.backend_git_sha,
        architecture_git_sha=args.architecture_git_sha,
        infrastructure_git_sha=args.infrastructure_git_sha,
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

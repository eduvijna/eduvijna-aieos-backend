"""Verify sanitized pre-publication Backend OCI provenance receipt (WPI-OCI-I01)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from backend_oci_common import (
    ARTIFACT_KIND,
    BASE_IMAGE_DIGEST,
    CLASSIFICATION,
    EXPECTED_BUILD_PLATFORM,
    EXPECTED_CLASSIFICATION_LABEL,
    EXPECTED_IMAGE_SOURCE,
    EXPECTED_PYTHON_VERSION,
    EXPECTED_RUNTIME_USER,
    EXPECTED_UV_VERSION,
    FORBIDDEN_RECEIPT_FIELDS,
    REQUIRED_OCI_LABELS,
    REQUIRED_RECEIPT_FIELDS,
    SCHEMA_VERSION,
    SOURCE_REPOSITORY,
    assert_default_command,
    reject_secret_like_values,
    require_digest,
    require_full_git_sha,
    require_sha256_hex,
)


def verify_receipt(receipt: dict[str, Any]) -> None:
    if not isinstance(receipt, dict):
        raise ValueError("receipt must be an object")

    unknown = set(receipt.keys()) - REQUIRED_RECEIPT_FIELDS
    if unknown:
        raise ValueError(f"unknown top-level fields: {sorted(unknown)}")
    missing = REQUIRED_RECEIPT_FIELDS - set(receipt.keys())
    if missing:
        raise ValueError(f"missing required fields: {sorted(missing)}")

    for field in receipt:
        if field.lower() in FORBIDDEN_RECEIPT_FIELDS or field in FORBIDDEN_RECEIPT_FIELDS:
            raise ValueError(f"forbidden field: {field}")

    if receipt["schema_version"] != SCHEMA_VERSION:
        raise ValueError("schema_version mismatch")
    if receipt["artifact_kind"] != ARTIFACT_KIND:
        raise ValueError("artifact_kind mismatch")
    if receipt["classification"] != CLASSIFICATION:
        raise ValueError("classification mismatch")
    if receipt["source_repository"] != SOURCE_REPOSITORY:
        raise ValueError("source_repository mismatch")

    backend = require_full_git_sha(receipt["backend_git_sha"], label="backend_git_sha")
    architecture = require_full_git_sha(
        receipt["architecture_git_sha"], label="architecture_git_sha"
    )
    infrastructure = require_full_git_sha(
        receipt["infrastructure_git_sha"], label="infrastructure_git_sha"
    )

    if receipt["python_version"] != EXPECTED_PYTHON_VERSION:
        raise ValueError("python_version mismatch")
    if receipt["uv_version"] != EXPECTED_UV_VERSION:
        raise ValueError("uv_version mismatch")
    if receipt["build_platform"] != EXPECTED_BUILD_PLATFORM:
        raise ValueError("build_platform mismatch")

    require_sha256_hex(receipt["dockerfile_sha256"], label="dockerfile_sha256")
    require_sha256_hex(receipt["uv_lock_sha256"], label="uv_lock_sha256")

    base = receipt["base_image"]
    if not isinstance(base, dict):
        raise ValueError("base_image must be object")
    if set(base.keys()) != {"reference", "digest"}:
        raise ValueError("base_image fields must be exactly reference,digest")
    if "@sha256:" not in str(base.get("reference", "")):
        raise ValueError("base image reference must be digest-pinned")
    digest = require_digest(base["digest"], label="base_image.digest")
    if digest != BASE_IMAGE_DIGEST:
        raise ValueError("base_image.digest mismatch")

    config_id = receipt["image_config_id"]
    if not isinstance(config_id, str) or not config_id:
        raise ValueError("image_config_id must be non-empty string (local config identity)")
    # Explicitly not a registry manifest authority field.
    if "manifest_digest" in receipt:
        raise ValueError("manifest_digest forbidden pre-publication")

    labels = receipt["oci_labels"]
    if not isinstance(labels, dict):
        raise ValueError("oci_labels must be object")
    for key in REQUIRED_OCI_LABELS:
        if key not in labels or not isinstance(labels[key], str) or not labels[key]:
            raise ValueError(f"missing OCI label: {key}")
    if labels["org.opencontainers.image.source"] != EXPECTED_IMAGE_SOURCE:
        raise ValueError("source label mismatch")
    if labels["org.opencontainers.image.revision"] != backend:
        raise ValueError("revision label != backend_git_sha")
    if labels["io.eduvijna.aieos.git_revision"] != backend:
        raise ValueError("git_revision label != backend_git_sha")
    if labels["io.eduvijna.aieos.architecture_revision"] != architecture:
        raise ValueError("architecture_revision label mismatch")
    if labels["io.eduvijna.aieos.infrastructure_revision"] != infrastructure:
        raise ValueError("infrastructure_revision label mismatch")
    if labels["io.eduvijna.aieos.classification"] != EXPECTED_CLASSIFICATION_LABEL:
        raise ValueError("classification label mismatch")
    if labels["org.opencontainers.image.version"] != receipt["application_version"]:
        raise ValueError("version label mismatch")
    if labels["io.eduvijna.aieos.application_version"] != receipt["application_version"]:
        raise ValueError("application_version label mismatch")

    if receipt["runtime_user"] != EXPECTED_RUNTIME_USER:
        raise ValueError("runtime_user mismatch")
    assert_default_command(receipt["default_command"])

    if receipt["source_clean"] is not True:
        raise ValueError("source_clean must be true")
    if receipt["validation_status"] != "PASS":
        raise ValueError("validation_status must be PASS")
    if receipt["publication_performed"] is not False:
        raise ValueError("publication_performed must be false")
    if receipt["publication_authorized"] is not False:
        raise ValueError("publication_authorized must be false")
    if receipt["deployment_authorized"] is not False:
        raise ValueError("deployment_authorized must be false")

    reject_secret_like_values(receipt)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify Backend OCI pre-publication provenance")
    parser.add_argument("--receipt", required=True)
    args = parser.parse_args(argv)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    path = Path(args.receipt)
    receipt = json.loads(path.read_text(encoding="utf-8"))
    verify_receipt(receipt)
    print("VERIFY_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

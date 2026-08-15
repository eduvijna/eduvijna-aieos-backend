#!/usr/bin/env python3
"""Verify an immutable NON_PRODUCTION AIEOS verified build bundle (PED-I04)."""

from __future__ import annotations

import argparse
import json
import sys
import tarfile
from pathlib import Path

_TOOLS_RELEASE = Path(__file__).resolve().parent
if str(_TOOLS_RELEASE) not in sys.path:
    sys.path.insert(0, str(_TOOLS_RELEASE))

from common import (
    ARTIFACT_KIND,
    CLASSIFICATION,
    EXPECTED_MIGRATION_HEAD,
    EXPECTED_OPENAPI_SHA256,
    REQUIRED_MANIFEST_FIELDS,
    SCHEMA_VERSION,
    is_unsafe_tar_member,
    require_full_git_sha,
    sha256_bytes,
)


class BundleVerificationError(ValueError):
    """Verified build bundle failed validation."""


def _load_manifest(raw: bytes) -> dict:
    try:
        text = raw.decode("utf-8")
        manifest = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BundleVerificationError("manifest malformed") from exc
    if not isinstance(manifest, dict):
        raise BundleVerificationError("manifest malformed")
    for field in REQUIRED_MANIFEST_FIELDS:
        if field not in manifest:
            raise BundleVerificationError(f"required manifest field missing: {field}")
    return manifest


def verify_verified_bundle(
    bundle_path: Path,
    *,
    expected_git_sha: str | None = None,
) -> dict:
    if not bundle_path.is_file():
        raise BundleVerificationError("bundle file missing")

    with tarfile.open(bundle_path, mode="r:") as archive:
        members = archive.getmembers()
        names = [m.name.replace("\\", "/") for m in members]
        for name in names:
            if is_unsafe_tar_member(name):
                raise BundleVerificationError(f"unsafe path in bundle: {name}")
            if name.endswith("/") or name == "":
                raise BundleVerificationError(f"unexpected directory member: {name}")

        by_name = {m.name.replace("\\", "/"): m for m in members}
        required = {
            "verified-build-manifest.json",
            "contracts/openapi/aieos-v1.json",
            "uv.lock",
        }
        missing = required - set(by_name)
        if missing:
            raise BundleVerificationError(f"missing file(s): {sorted(missing)}")

        # Exactly one wheel and one sdist under dist/
        dist_files = [n for n in by_name if n.startswith("dist/")]
        wheels = [n for n in dist_files if n.endswith(".whl")]
        sdists = [n for n in dist_files if n.endswith(".tar.gz")]
        if len(wheels) != 1 or len(sdists) != 1:
            raise BundleVerificationError("expected exactly one wheel and one sdist")
        unexpected = set(by_name) - required - {wheels[0], sdists[0]}
        if unexpected:
            raise BundleVerificationError(
                f"unexpected unsafe/extra file(s): {sorted(unexpected)}"
            )

        def read_member(name: str) -> bytes:
            member = by_name[name]
            if not member.isfile():
                raise BundleVerificationError(f"member is not a regular file: {name}")
            extracted = archive.extractfile(member)
            if extracted is None:
                raise BundleVerificationError(f"unable to read member: {name}")
            with extracted:
                return extracted.read()

        manifest = _load_manifest(read_member("verified-build-manifest.json"))
        if manifest["schema_version"] != SCHEMA_VERSION:
            raise BundleVerificationError("unsupported schema_version")
        if manifest["artifact_kind"] != ARTIFACT_KIND:
            raise BundleVerificationError("unexpected artifact_kind")
        if manifest["classification"] != CLASSIFICATION:
            raise BundleVerificationError("unexpected classification")
        if manifest.get("production_authorized") is not False:
            raise BundleVerificationError("production_authorized must be false")
        if manifest.get("deployable") is not False:
            raise BundleVerificationError("deployable must be false")
        if manifest.get("mutation_authorized") is not False:
            raise BundleVerificationError("mutation_authorized must be false")

        git_sha = require_full_git_sha(str(manifest["git_sha"]))
        if expected_git_sha is not None:
            expected = require_full_git_sha(expected_git_sha)
            if git_sha != expected:
                raise BundleVerificationError("manifest Git SHA differs from expected SHA")

        if manifest["migration_head"] != EXPECTED_MIGRATION_HEAD:
            raise BundleVerificationError("wrong migration head")
        if str(manifest["openapi_sha256"]).upper() != EXPECTED_OPENAPI_SHA256:
            raise BundleVerificationError("OpenAPI SHA mismatch")

        openapi_bytes = read_member("contracts/openapi/aieos-v1.json")
        if sha256_bytes(openapi_bytes) != EXPECTED_OPENAPI_SHA256:
            raise BundleVerificationError("tampered OpenAPI rejected")
        if sha256_bytes(openapi_bytes) != str(manifest["openapi_sha256"]).upper():
            raise BundleVerificationError("OpenAPI SHA mismatch")

        lock_bytes = read_member("uv.lock")
        if sha256_bytes(lock_bytes) != str(manifest["uv_lock_sha256"]).upper():
            raise BundleVerificationError("uv.lock SHA mismatch")

        wheel_name = f"dist/{manifest['wheel']['filename']}"
        sdist_name = f"dist/{manifest['sdist']['filename']}"
        if wheel_name not in by_name:
            raise BundleVerificationError("wheel missing")
        if sdist_name not in by_name:
            raise BundleVerificationError("sdist missing")
        wheel_bytes = read_member(wheel_name)
        sdist_bytes = read_member(sdist_name)
        if sha256_bytes(wheel_bytes) != str(manifest["wheel"]["sha256"]).upper():
            raise BundleVerificationError("wheel SHA mismatch")
        if sha256_bytes(sdist_bytes) != str(manifest["sdist"]["sha256"]).upper():
            raise BundleVerificationError("sdist SHA mismatch")

    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--expected-git-sha", default=None)
    args = parser.parse_args(argv)
    try:
        manifest = verify_verified_bundle(
            args.bundle, expected_git_sha=args.expected_git_sha
        )
    except (BundleVerificationError, ValueError) as exc:
        print(f"verification_failed={exc}", file=sys.stderr)
        return 1
    print("verification_ok=true")
    print(f"git_sha={manifest['git_sha']}")
    print(f"application_version={manifest['application_version']}")
    print(f"migration_head={manifest['migration_head']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Build an immutable NON_PRODUCTION verified AIEOS Python build bundle (PED-I04)."""

from __future__ import annotations

import argparse
import platform
import subprocess
import sys
from pathlib import Path

_TOOLS_RELEASE = Path(__file__).resolve().parent
if str(_TOOLS_RELEASE) not in sys.path:
    sys.path.insert(0, str(_TOOLS_RELEASE))

from common import (
    ARTIFACT_KIND,
    CLASSIFICATION,
    EXPECTED_APPLICATION_VERSION,
    REPOSITORY,
    SCHEMA_VERSION,
    add_deterministic_tar_file,
    assert_openapi_digest,
    assert_version_coherence,
    canonical_manifest_json,
    derive_and_validate_migration_head,
    require_full_git_sha,
    sha256_bytes,
    sha256_file,
)


def _repo_root_from_here() -> Path:
    return Path(__file__).resolve().parents[2]


def _find_one(dist_dir: Path, pattern: str) -> Path:
    matches = sorted(dist_dir.glob(pattern))
    if len(matches) != 1:
        raise ValueError(f"expected exactly one {pattern} in {dist_dir}, got {matches}")
    return matches[0]


def build_bundle(
    *,
    repo_root: Path,
    git_sha: str,
    python_version: str,
    uv_version: str,
    dist_dir: Path | None = None,
    output_dir: Path | None = None,
) -> tuple[Path, str, dict]:
    git_sha = require_full_git_sha(git_sha)
    version = assert_version_coherence(repo_root)
    if version != EXPECTED_APPLICATION_VERSION:
        raise ValueError(
            f"application version {version} != expected {EXPECTED_APPLICATION_VERSION}"
        )

    dist_dir = dist_dir or (repo_root / "dist")
    output_dir = output_dir or (repo_root / "build")
    output_dir.mkdir(parents=True, exist_ok=True)

    wheel = _find_one(dist_dir, "*.whl")
    sdist = _find_one(dist_dir, "*.tar.gz")
    openapi = repo_root / "contracts" / "openapi" / "aieos-v1.json"
    lockfile = repo_root / "uv.lock"

    openapi_sha = assert_openapi_digest(repo_root)
    lock_sha = sha256_file(lockfile)
    migration_head = derive_and_validate_migration_head(repo_root)
    wheel_sha = sha256_file(wheel)
    sdist_sha = sha256_file(sdist)

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "artifact_kind": ARTIFACT_KIND,
        "classification": CLASSIFICATION,
        "repository": REPOSITORY,
        "application_version": version,
        "git_sha": git_sha,
        "python_version": python_version,
        "uv_version": uv_version,
        "migration_head": migration_head,
        "openapi_sha256": openapi_sha,
        "uv_lock_sha256": lock_sha,
        "wheel": {"filename": wheel.name, "sha256": wheel_sha},
        "sdist": {"filename": sdist.name, "sha256": sdist_sha},
        "production_authorized": False,
        "deployable": False,
        "mutation_authorized": False,
    }

    manifest_path = output_dir / "verified-build-manifest.json"
    manifest_text = canonical_manifest_json(manifest)
    manifest_path.write_text(manifest_text, encoding="utf-8", newline="\n")

    bundle_name = f"aieos-{version}-{git_sha}.tar"
    bundle_path = output_dir / bundle_name
    if bundle_path.exists():
        bundle_path.unlink()

    members = [
        (f"dist/{wheel.name}", wheel),
        (f"dist/{sdist.name}", sdist),
        ("verified-build-manifest.json", manifest_path),
        ("contracts/openapi/aieos-v1.json", openapi),
        ("uv.lock", lockfile),
    ]
    members.sort(key=lambda item: item[0])

    import tarfile

    with tarfile.open(bundle_path, mode="w") as archive:
        for arcname, source in members:
            add_deterministic_tar_file(archive, arcname, source)

    bundle_digest = sha256_bytes(bundle_path.read_bytes())
    return bundle_path, bundle_digest, manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=_repo_root_from_here())
    parser.add_argument("--git-sha", required=True)
    parser.add_argument(
        "--python-version",
        default=".".join(platform.python_version_tuple()[:3]),
    )
    parser.add_argument("--uv-version", default="")
    parser.add_argument("--dist-dir", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    args = parser.parse_args(argv)

    uv_version = args.uv_version
    if not uv_version:
        proc = subprocess.run(
            ["uv", "--version"],
            check=True,
            capture_output=True,
            text=True,
        )
        # Example: "uv 0.12.4 (...)"
        uv_version = proc.stdout.strip().split()[1]

    bundle_path, digest, manifest = build_bundle(
        repo_root=args.repo_root.resolve(),
        git_sha=args.git_sha,
        python_version=args.python_version,
        uv_version=uv_version,
        dist_dir=args.dist_dir,
        output_dir=args.output_dir,
    )
    print(f"bundle_path={bundle_path}")
    print(f"bundle_sha256={digest}")
    print(f"git_sha={manifest['git_sha']}")
    print(f"application_version={manifest['application_version']}")
    print(f"migration_head={manifest['migration_head']}")
    print(f"openapi_sha256={manifest['openapi_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

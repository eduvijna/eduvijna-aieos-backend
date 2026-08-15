"""PED-I04 verified build bundle unit tests."""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from tests.dbutil import REPO_ROOT

TOOLS_RELEASE = REPO_ROOT / "tools" / "release"
sys.path.insert(0, str(TOOLS_RELEASE))

from build_verified_bundle import build_bundle  # noqa: E402
from common import (  # noqa: E402
    EXPECTED_APPLICATION_VERSION,
    EXPECTED_MIGRATION_HEAD,
    EXPECTED_OPENAPI_SHA256,
    REPOSITORY,
    assert_version_coherence,
    canonical_manifest_json,
    sha256_bytes,
)
from verify_verified_bundle import (  # noqa: E402
    BundleVerificationError,
    verify_verified_bundle,
)

pytestmark = pytest.mark.ped_i04

VALID_SHA = "a" * 40


@pytest.fixture(scope="module")
def dist_artifacts(tmp_path_factory) -> Path:
    """Build wheel/sdist once into a temp dist for PED-I04 tests."""
    dist_dir = tmp_path_factory.mktemp("dist")
    subprocess.run(
        ["uv", "build", "--out-dir", str(dist_dir)],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert list(dist_dir.glob("*.whl"))
    assert list(dist_dir.glob("*.tar.gz"))
    return dist_dir


@pytest.fixture
def valid_bundle(tmp_path: Path, dist_artifacts: Path) -> tuple[Path, dict]:
    out = tmp_path / "out"
    # Copy dist so tests can mutate without affecting module fixture.
    local_dist = tmp_path / "dist"
    shutil.copytree(dist_artifacts, local_dist)
    path, digest, manifest = build_bundle(
        repo_root=REPO_ROOT,
        git_sha=VALID_SHA,
        python_version="3.14.7",
        uv_version="0.12.4",
        dist_dir=local_dist,
        output_dir=out,
    )
    assert digest == sha256_bytes(path.read_bytes())
    return path, manifest


def _replace_member(bundle: Path, member_name: str, new_bytes: bytes, dest: Path) -> Path:
    with tarfile.open(bundle, mode="r:") as src, tarfile.open(dest, mode="w") as dst:
        for member in src.getmembers():
            name = member.name.replace("\\", "/")
            if name == member_name:
                info = tarfile.TarInfo(name=name)
                info.size = len(new_bytes)
                info.mtime = 0
                info.uid = 0
                info.gid = 0
                info.mode = 0o644
                dst.addfile(info, io.BytesIO(new_bytes))
            else:
                extracted = src.extractfile(member)
                assert extracted is not None
                data = extracted.read()
                info = tarfile.TarInfo(name=name)
                info.size = len(data)
                info.mtime = 0
                info.uid = 0
                info.gid = 0
                info.mode = 0o644
                dst.addfile(info, io.BytesIO(data))
    return dest


class TestVersionCoherence:
    def test_version_matches_pyproject(self) -> None:
        assert assert_version_coherence(REPO_ROOT) == "0.1.0"


class TestValidBundle:
    def test_valid_manifest_and_bundle(self, valid_bundle) -> None:
        path, manifest = valid_bundle
        loaded = verify_verified_bundle(path, expected_git_sha=VALID_SHA)
        assert loaded["git_sha"] == VALID_SHA
        assert loaded["application_version"] == EXPECTED_APPLICATION_VERSION
        assert loaded["repository"] == REPOSITORY
        assert loaded["migration_head"] == EXPECTED_MIGRATION_HEAD
        assert loaded["openapi_sha256"] == EXPECTED_OPENAPI_SHA256
        assert loaded["classification"] == "NON_PRODUCTION"
        assert loaded["production_authorized"] is False
        assert loaded["deployable"] is False
        assert loaded["mutation_authorized"] is False
        assert manifest["wheel"]["filename"].endswith(".whl")
        assert manifest["sdist"]["filename"].endswith(".tar.gz")


class TestTampering:
    def test_tampered_wheel_rejected(self, valid_bundle, tmp_path) -> None:
        path, manifest = valid_bundle
        wheel = f"dist/{manifest['wheel']['filename']}"
        bad = _replace_member(path, wheel, b"TAMPERED-WHEEL", tmp_path / "bad-wheel.tar")
        with pytest.raises(BundleVerificationError, match="wheel SHA"):
            verify_verified_bundle(bad, expected_git_sha=VALID_SHA)

    def test_tampered_sdist_rejected(self, valid_bundle, tmp_path) -> None:
        path, manifest = valid_bundle
        sdist = f"dist/{manifest['sdist']['filename']}"
        bad = _replace_member(path, sdist, b"TAMPERED-SDIST", tmp_path / "bad-sdist.tar")
        with pytest.raises(BundleVerificationError, match="sdist SHA"):
            verify_verified_bundle(bad, expected_git_sha=VALID_SHA)

    def test_tampered_openapi_rejected(self, valid_bundle, tmp_path) -> None:
        path, _manifest = valid_bundle
        bad = _replace_member(
            path,
            "contracts/openapi/aieos-v1.json",
            b'{"tampered":true}\n',
            tmp_path / "bad-openapi.tar",
        )
        with pytest.raises(BundleVerificationError, match="OpenAPI|tampered"):
            verify_verified_bundle(bad, expected_git_sha=VALID_SHA)

    def test_tampered_uv_lock_rejected(self, valid_bundle, tmp_path) -> None:
        path, _manifest = valid_bundle
        bad = _replace_member(path, "uv.lock", b"tampered-lock\n", tmp_path / "bad-lock.tar")
        with pytest.raises(BundleVerificationError, match="uv.lock"):
            verify_verified_bundle(bad, expected_git_sha=VALID_SHA)

    def test_wrong_git_sha_rejected(self, valid_bundle) -> None:
        path, _manifest = valid_bundle
        with pytest.raises(BundleVerificationError, match="Git SHA"):
            verify_verified_bundle(path, expected_git_sha="b" * 40)

    def test_short_git_sha_rejected(self, tmp_path, dist_artifacts) -> None:
        local_dist = tmp_path / "dist-short"
        shutil.copytree(dist_artifacts, local_dist)
        with pytest.raises(ValueError, match="40-character"):
            build_bundle(
                repo_root=REPO_ROOT,
                git_sha="abc1234",
                python_version="3.14.7",
                uv_version="0.12.4",
                dist_dir=local_dist,
                output_dir=tmp_path / "out-short",
            )

    def test_wrong_migration_head_rejected(self, valid_bundle, tmp_path) -> None:
        path, manifest = valid_bundle
        bad_manifest = dict(manifest)
        bad_manifest["migration_head"] = "gcii130001"
        raw = canonical_manifest_json(bad_manifest).encode("utf-8")
        bad = _replace_member(
            path, "verified-build-manifest.json", raw, tmp_path / "bad-head.tar"
        )
        with pytest.raises(BundleVerificationError, match="migration head"):
            verify_verified_bundle(bad, expected_git_sha=VALID_SHA)

    def test_tampered_application_version_rejected(self, valid_bundle, tmp_path) -> None:
        path, manifest = valid_bundle
        bad_manifest = dict(manifest)
        bad_manifest["application_version"] = "999.0.0"
        raw = canonical_manifest_json(bad_manifest).encode("utf-8")
        bad = _replace_member(
            path,
            "verified-build-manifest.json",
            raw,
            tmp_path / "bad-version.tar",
        )
        with pytest.raises(BundleVerificationError, match="application_version"):
            verify_verified_bundle(bad, expected_git_sha=VALID_SHA)

    def test_tampered_repository_rejected(self, valid_bundle, tmp_path) -> None:
        path, manifest = valid_bundle
        bad_manifest = dict(manifest)
        bad_manifest["repository"] = "attacker/example"
        raw = canonical_manifest_json(bad_manifest).encode("utf-8")
        bad = _replace_member(
            path,
            "verified-build-manifest.json",
            raw,
            tmp_path / "bad-repo.tar",
        )
        with pytest.raises(BundleVerificationError, match="repository"):
            verify_verified_bundle(bad, expected_git_sha=VALID_SHA)

    def test_malformed_manifest_rejected(self, valid_bundle, tmp_path) -> None:
        path, _manifest = valid_bundle
        bad = _replace_member(
            path,
            "verified-build-manifest.json",
            b"{not-json",
            tmp_path / "bad-manifest.tar",
        )
        with pytest.raises(BundleVerificationError, match="malformed"):
            verify_verified_bundle(bad, expected_git_sha=VALID_SHA)

    def test_missing_file_rejected(self, valid_bundle, tmp_path) -> None:
        path, _manifest = valid_bundle
        dest = tmp_path / "missing.tar"
        with tarfile.open(path, mode="r:") as src, tarfile.open(dest, mode="w") as dst:
            for member in src.getmembers():
                name = member.name.replace("\\", "/")
                if name == "uv.lock":
                    continue
                extracted = src.extractfile(member)
                assert extracted is not None
                data = extracted.read()
                info = tarfile.TarInfo(name=name)
                info.size = len(data)
                info.mtime = 0
                dst.addfile(info, io.BytesIO(data))
        with pytest.raises(BundleVerificationError, match="missing"):
            verify_verified_bundle(dest, expected_git_sha=VALID_SHA)

    def test_extra_unsafe_file_rejected(self, valid_bundle, tmp_path) -> None:
        path, _manifest = valid_bundle
        dest = tmp_path / "extra.tar"
        with tarfile.open(path, mode="r:") as src, tarfile.open(dest, mode="w") as dst:
            for member in src.getmembers():
                extracted = src.extractfile(member)
                assert extracted is not None
                data = extracted.read()
                info = tarfile.TarInfo(name=member.name.replace("\\", "/"))
                info.size = len(data)
                info.mtime = 0
                dst.addfile(info, io.BytesIO(data))
            extra = b"secret\n"
            info = tarfile.TarInfo(name=".env")
            info.size = len(extra)
            info.mtime = 0
            dst.addfile(info, io.BytesIO(extra))
        with pytest.raises(BundleVerificationError, match="unexpected"):
            verify_verified_bundle(dest, expected_git_sha=VALID_SHA)

    def test_path_traversal_rejected(self, valid_bundle, tmp_path) -> None:
        path, _manifest = valid_bundle
        dest = tmp_path / "traverse.tar"
        with tarfile.open(path, mode="r:") as src, tarfile.open(dest, mode="w") as dst:
            for member in src.getmembers():
                extracted = src.extractfile(member)
                assert extracted is not None
                data = extracted.read()
                info = tarfile.TarInfo(name=member.name.replace("\\", "/"))
                info.size = len(data)
                info.mtime = 0
                dst.addfile(info, io.BytesIO(data))
            payload = b"x"
            info = tarfile.TarInfo(name="../escape.txt")
            info.size = len(payload)
            info.mtime = 0
            dst.addfile(info, io.BytesIO(payload))
        with pytest.raises(BundleVerificationError, match="unsafe path"):
            verify_verified_bundle(dest, expected_git_sha=VALID_SHA)

"""Shared constants and helpers for PED-I04 verified build bundles (stdlib only)."""

from __future__ import annotations

import hashlib
import json
import re
import tarfile
from pathlib import Path

SCHEMA_VERSION = 1
ARTIFACT_KIND = "AIEOS_VERIFIED_PYTHON_BUILD_BUNDLE"
CLASSIFICATION = "NON_PRODUCTION"
REPOSITORY = "eduvijna/eduvijna-aieos-backend"

EXPECTED_OPENAPI_SHA256 = (
    "824B389D6D4EDB2EA5D8ED3A9E5411087B566DFDCA09C2AB0CD4FDED51C4D89D"
)
EXPECTED_MIGRATION_HEAD = "tosd090001"
EXPECTED_APPLICATION_VERSION = "0.1.0"

_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_REVISION = re.compile(
    r"^revision(?:\s*:\s*str)?\s*=\s*['\"]([A-Za-z0-9_]+)['\"]",
    re.MULTILINE,
)
_DOWN_REVISION = re.compile(
    r"^down_revision(?:\s*:\s*str\s*\|\s*None)?\s*=\s*"
    r"(None|['\"]([A-Za-z0-9_]+)['\"])",
    re.MULTILINE,
)
_PYPROJECT_VERSION = re.compile(
    r'(?m)^version\s*=\s*"([^"]+)"',
)
_EXPECTED_ALEMBIC_HEAD_SRC = re.compile(
    r'EXPECTED_ALEMBIC_HEAD\s*=\s*"([A-Za-z0-9_]+)"',
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest().upper()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def require_full_git_sha(value: str) -> str:
    if not _GIT_SHA.fullmatch(value):
        raise ValueError(
            "git_sha must be an exact 40-character lowercase hexadecimal SHA"
        )
    return value


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
        raise ValueError(
            f"VERSION ({version}) != pyproject.toml version ({pyproject})"
        )
    return version


def derive_migration_heads(repo_root: Path) -> set[str]:
    versions_dir = repo_root / "migrations" / "versions"
    revisions: dict[str, str | None] = {}
    for path in sorted(versions_dir.glob("*.py")):
        if path.name == "__init__.py":
            continue
        text = path.read_text(encoding="utf-8")
        rev_match = _REVISION.search(text)
        if rev_match is None:
            raise ValueError(f"migration missing revision: {path.name}")
        revision = rev_match.group(1)
        down_match = _DOWN_REVISION.search(text)
        if down_match is None:
            raise ValueError(f"migration missing down_revision: {path.name}")
        if down_match.group(1) == "None":
            down: str | None = None
        else:
            down = down_match.group(2)
        revisions[revision] = down
    pointed_to = {down for down in revisions.values() if down is not None}
    return set(revisions) - pointed_to


def derive_and_validate_migration_head(repo_root: Path) -> str:
    heads = derive_migration_heads(repo_root)
    if len(heads) != 1:
        raise ValueError(f"expected exactly one Alembic head, got {sorted(heads)}")
    head = next(iter(heads))
    if head != EXPECTED_MIGRATION_HEAD:
        raise ValueError(
            f"migration head {head} != expected {EXPECTED_MIGRATION_HEAD}"
        )
    readiness = (
        repo_root
        / "src"
        / "aieos"
        / "platform"
        / "runtime"
        / "readiness.py"
    ).read_text(encoding="utf-8")
    match = _EXPECTED_ALEMBIC_HEAD_SRC.search(readiness)
    if match is None:
        raise ValueError("EXPECTED_ALEMBIC_HEAD missing from readiness.py")
    if match.group(1) != head:
        raise ValueError(
            "PED-I02 EXPECTED_ALEMBIC_HEAD is stale relative to ScriptDirectory head"
        )
    return head


def assert_openapi_digest(repo_root: Path) -> str:
    path = repo_root / "contracts" / "openapi" / "aieos-v1.json"
    digest = sha256_file(path)
    if digest != EXPECTED_OPENAPI_SHA256:
        raise ValueError(
            f"OpenAPI SHA256 {digest} != expected {EXPECTED_OPENAPI_SHA256}"
        )
    return digest


def canonical_manifest_json(manifest: dict) -> str:
    return json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"


def is_unsafe_tar_member(name: str) -> bool:
    normalized = name.replace("\\", "/")
    if normalized.startswith("/") or normalized.startswith("../") or "/../" in normalized:
        return True
    if normalized == ".." or normalized.endswith("/.."):
        return True
    if ":" in normalized.split("/")[0]:
        # Windows drive / absolute-like
        return True
    return False


def add_deterministic_tar_file(
    archive: tarfile.TarFile,
    arcname: str,
    source: Path,
) -> None:
    if is_unsafe_tar_member(arcname):
        raise ValueError(f"unsafe tar member name: {arcname}")
    info = tarfile.TarInfo(name=arcname)
    data = source.read_bytes()
    info.size = len(data)
    info.mtime = 0
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mode = 0o644
    import io

    archive.addfile(info, io.BytesIO(data))


REQUIRED_MANIFEST_FIELDS = (
    "schema_version",
    "artifact_kind",
    "classification",
    "repository",
    "application_version",
    "git_sha",
    "python_version",
    "uv_version",
    "migration_head",
    "openapi_sha256",
    "uv_lock_sha256",
    "wheel",
    "sdist",
    "production_authorized",
    "deployable",
    "mutation_authorized",
)

"""Local-only API runtime environment constants.

LOCAL DEVELOPMENT ONLY — NEVER PRODUCTION.
"""

from __future__ import annotations

import base64
import re
import subprocess
import uuid
from collections.abc import Mapping
from pathlib import Path

from tools.dev.constants import (
    DB_PASSWORD,
    HOST,
    HOST_PORT,
    MIGRATOR_USER,
    RUNTIME_USER,
    SCHEMA_OWNER_ROLE,
    SECURITY_SCHEMA_OWNER_ROLE,
)

# Deterministic local teacher / tenant identity for Swagger and route exercise.
LOCAL_DEV_NAMESPACE = uuid.UUID("f47ac10b-58cc-4372-a567-0e02b2c3d479")
LOCAL_DEV_TENANT_ID = uuid.uuid5(LOCAL_DEV_NAMESPACE, "aieos.local-f5.tenant")
LOCAL_DEV_PRINCIPAL_ID = uuid.uuid5(LOCAL_DEV_NAMESPACE, "aieos.local-f5.principal")

LOCAL_BEARER_TOKEN = "aieos-local-dev"

LOCAL_ARTIFACT_DIGEST = "sha256:" + ("0" * 64)
LOCAL_CURSOR_SIGNING_KEY = b"aieos-local-f5-cursor-signing-key"
LOCAL_CURSOR_SIGNING_KEY_B64 = base64.b64encode(LOCAL_CURSOR_SIGNING_KEY).decode("ascii")

# Placeholder HTTPS auth contract values — local launcher does not call JWKS.
LOCAL_AUTH_ISSUER = "https://local-dev.aieos.invalid/"
LOCAL_AUTH_AUDIENCE = "aieos-api"
LOCAL_AUTH_JWKS_URI = "https://local-dev.aieos.invalid/.well-known/jwks.json"

API_BIND_HOST = "127.0.0.1"
API_BIND_PORT = 8080

_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _validate_source_sha(source_sha: str) -> str:
    sha = source_sha.strip()
    if not _GIT_SHA.fullmatch(sha):
        raise ValueError(
            "source Git SHA must be exactly 40 lowercase hexadecimal characters"
        )
    return sha


def resolve_local_source_git_sha(*, repo_root: Path | None = None) -> str:
    """Resolve the exact current repository HEAD for local F5 release identity."""
    root = _REPO_ROOT if repo_root is None else repo_root
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "local API startup requires git; git executable was not found"
        ) from exc
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip() if exc.stderr else ""
        detail = f": {stderr}" if stderr else ""
        raise RuntimeError(
            f"local API startup requires git rev-parse HEAD{detail}"
        ) from exc
    return _validate_source_sha(result.stdout)


def is_local_worktree_dirty(*, repo_root: Path | None = None) -> bool:
    """Return True when the repository worktree has uncommitted changes."""
    root = _REPO_ROOT if repo_root is None else repo_root
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False
    return bool(result.stdout.strip())


def build_local_api_environ(source_sha: str) -> dict[str, str]:
    """Return safe local-development process environment for ApiRuntimeConfig."""
    git_sha = _validate_source_sha(source_sha)
    runtime_url = (
        f"postgresql+psycopg://{RUNTIME_USER}:{DB_PASSWORD}"
        f"@{HOST}:{HOST_PORT}/aieos"
    )
    return {
        "AIEOS_DEPLOYMENT_ENVIRONMENT": "STAGING",
        "AIEOS_RELEASE_VERSION": "0.1.0",
        "AIEOS_GIT_SHA": git_sha,
        "AIEOS_BUILD_ID": "local-f5-dev",
        "AIEOS_ARTIFACT_DIGEST": LOCAL_ARTIFACT_DIGEST,
        "AIEOS_RUNTIME_DATABASE_URL": runtime_url,
        "AIEOS_RUNTIME_DATABASE_ROLE": RUNTIME_USER,
        "AIEOS_SCHEMA_OWNER_ROLE": SCHEMA_OWNER_ROLE,
        "AIEOS_SECURITY_SCHEMA_OWNER_ROLE": SECURITY_SCHEMA_OWNER_ROLE,
        "AIEOS_MIGRATOR_ROLE": MIGRATOR_USER,
        "AIEOS_CURSOR_SIGNING_KEY_B64": LOCAL_CURSOR_SIGNING_KEY_B64,
        "AIEOS_IDEMPOTENCY_RETENTION_SECONDS": "86400",
        "AIEOS_RUNTIME_DATABASE_CONNECT_TIMEOUT_SECONDS": "5",
        "AIEOS_AUTH_ISSUER": LOCAL_AUTH_ISSUER,
        "AIEOS_AUTH_AUDIENCE": LOCAL_AUTH_AUDIENCE,
        "AIEOS_AUTH_JWKS_URI": LOCAL_AUTH_JWKS_URI,
        "AIEOS_API_MUTATION_ACTIVATION": "ENABLED",
        "AIEOS_API_MUTATION_AUTHORIZED_GIT_SHA": git_sha,
        "AIEOS_API_MUTATION_AUTHORIZED_ARTIFACT_DIGEST": LOCAL_ARTIFACT_DIGEST,
    }


def apply_local_api_environ(
    environ: Mapping[str, str] | None = None,
) -> tuple[dict[str, str], str]:
    """Merge local API environment into os.environ and return mapping + source SHA."""
    import os

    source_sha = resolve_local_source_git_sha()
    local_env = build_local_api_environ(source_sha)
    merged = dict(os.environ)
    merged.update(local_env)
    if environ is not None:
        merged.update(environ)
    for key, value in local_env.items():
        os.environ[key] = value
    for migrator_only_key in ("AIEOS_DATABASE_URL",):
        os.environ.pop(migrator_only_key, None)
        merged.pop(migrator_only_key, None)
    return merged, source_sha

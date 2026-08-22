"""Fail-closed STAGING/PRODUCTION API runtime configuration loader."""

from __future__ import annotations

import base64
import os
import re
from collections.abc import Mapping
from datetime import timedelta

from sqlalchemy.engine.url import make_url

from aieos.platform.runtime.errors import RuntimeConfigurationError
from aieos.platform.runtime.models import (
    ApiRuntimeConfig,
    DeploymentEnvironment,
    ReleaseIdentity,
)
from aieos.platform.security.auth_config import (
    AuthConfigurationError,
    load_auth_runtime_config,
)

ENV_DEPLOYMENT_ENVIRONMENT = "AIEOS_DEPLOYMENT_ENVIRONMENT"
ENV_RELEASE_VERSION = "AIEOS_RELEASE_VERSION"
ENV_GIT_SHA = "AIEOS_GIT_SHA"
ENV_BUILD_ID = "AIEOS_BUILD_ID"
ENV_ARTIFACT_DIGEST = "AIEOS_ARTIFACT_DIGEST"
ENV_RUNTIME_DATABASE_URL = "AIEOS_RUNTIME_DATABASE_URL"
ENV_RUNTIME_DATABASE_ROLE = "AIEOS_RUNTIME_DATABASE_ROLE"
ENV_SCHEMA_OWNER_ROLE = "AIEOS_SCHEMA_OWNER_ROLE"
ENV_SECURITY_SCHEMA_OWNER_ROLE = "AIEOS_SECURITY_SCHEMA_OWNER_ROLE"
ENV_MIGRATOR_ROLE = "AIEOS_MIGRATOR_ROLE"
ENV_CURSOR_SIGNING_KEY_B64 = "AIEOS_CURSOR_SIGNING_KEY_B64"
ENV_IDEMPOTENCY_RETENTION_SECONDS = "AIEOS_IDEMPOTENCY_RETENTION_SECONDS"
ENV_RUNTIME_DATABASE_CONNECT_TIMEOUT_SECONDS = (
    "AIEOS_RUNTIME_DATABASE_CONNECT_TIMEOUT_SECONDS"
)

# Alembic migrator DSN — must not be present in STAGING/PRODUCTION API runtime env.
ENV_MIGRATOR_DATABASE_URL = "AIEOS_DATABASE_URL"

_ROLE_NAME = re.compile(r"^[a-z_][a-z0-9_]*$")
_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_ARTIFACT_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
# Exact SQLAlchemy dialect for the installed Psycopg 3 dependency baseline.
REQUIRED_RUNTIME_DB_DRIVER = "postgresql+psycopg"
_REQUIRED_ENV = (
    ENV_DEPLOYMENT_ENVIRONMENT,
    ENV_RELEASE_VERSION,
    ENV_GIT_SHA,
    ENV_BUILD_ID,
    ENV_ARTIFACT_DIGEST,
    ENV_RUNTIME_DATABASE_URL,
    ENV_RUNTIME_DATABASE_ROLE,
    ENV_SCHEMA_OWNER_ROLE,
    ENV_SECURITY_SCHEMA_OWNER_ROLE,
    ENV_MIGRATOR_ROLE,
    ENV_CURSOR_SIGNING_KEY_B64,
    ENV_IDEMPOTENCY_RETENTION_SECONDS,
    ENV_RUNTIME_DATABASE_CONNECT_TIMEOUT_SECONDS,
)


def _require(environ: Mapping[str, str], name: str) -> str:
    raw = environ.get(name)
    if raw is None or raw.strip() == "":
        raise RuntimeConfigurationError(f"{name} is required and must be non-empty")
    return raw.strip()


def _parse_environment(value: str) -> DeploymentEnvironment:
    try:
        return DeploymentEnvironment(value)
    except ValueError as exc:
        raise RuntimeConfigurationError(
            f"{ENV_DEPLOYMENT_ENVIRONMENT} must be STAGING or PRODUCTION"
        ) from exc


def _parse_role(name: str, value: str) -> str:
    if not _ROLE_NAME.fullmatch(value):
        raise RuntimeConfigurationError(
            f"{name} must be a lowercase unquoted PostgreSQL identifier"
        )
    return value


def _parse_git_sha(value: str) -> str:
    if not _GIT_SHA.fullmatch(value):
        raise RuntimeConfigurationError(
            f"{ENV_GIT_SHA} must be an exact 40-character lowercase hexadecimal SHA"
        )
    return value


def _parse_artifact_digest(value: str) -> str:
    if not _ARTIFACT_DIGEST.fullmatch(value):
        raise RuntimeConfigurationError(
            f"{ENV_ARTIFACT_DIGEST} must be sha256:<64 lowercase hex characters>"
        )
    return value


def _parse_runtime_database_url(url_value: str, expected_role: str) -> str:
    try:
        url = make_url(url_value)
    except Exception as exc:
        raise RuntimeConfigurationError(
            f"{ENV_RUNTIME_DATABASE_URL} is not a valid SQLAlchemy database URL"
        ) from exc
    if url.drivername != REQUIRED_RUNTIME_DB_DRIVER:
        raise RuntimeConfigurationError(
            f"{ENV_RUNTIME_DATABASE_URL} must use the PostgreSQL + Psycopg 3 "
            f"SQLAlchemy driver ({REQUIRED_RUNTIME_DB_DRIVER})"
        )
    if not url.database:
        raise RuntimeConfigurationError(
            f"{ENV_RUNTIME_DATABASE_URL} must include a database name"
        )
    if not url.username:
        raise RuntimeConfigurationError(
            f"{ENV_RUNTIME_DATABASE_URL} must include a username"
        )
    if url.username != expected_role:
        raise RuntimeConfigurationError(
            f"{ENV_RUNTIME_DATABASE_URL} username must equal {ENV_RUNTIME_DATABASE_ROLE}"
        )
    return url_value


def _parse_cursor_signing_key(b64_value: str) -> bytes:
    try:
        decoded = base64.b64decode(b64_value, validate=True)
    except Exception as exc:
        raise RuntimeConfigurationError(
            f"{ENV_CURSOR_SIGNING_KEY_B64} must be valid Base64"
        ) from exc
    if not decoded:
        raise RuntimeConfigurationError(
            f"{ENV_CURSOR_SIGNING_KEY_B64} must decode to non-empty bytes"
        )
    return decoded


def _parse_idempotency_retention(raw: str) -> timedelta:
    if not re.fullmatch(r"[1-9][0-9]*", raw):
        raise RuntimeConfigurationError(
            f"{ENV_IDEMPOTENCY_RETENTION_SECONDS} must be a positive integer"
        )
    return timedelta(seconds=int(raw))


def _parse_connect_timeout(raw: str) -> int:
    if not re.fullmatch(r"[1-9][0-9]*", raw):
        raise RuntimeConfigurationError(
            f"{ENV_RUNTIME_DATABASE_CONNECT_TIMEOUT_SECONDS} must be a positive integer"
        )
    return int(raw)


def _assert_role_separation(
    *,
    runtime_role: str,
    content_owner: str,
    security_owner: str,
    migrator: str,
) -> None:
    pairs = (
        (runtime_role, content_owner, ENV_RUNTIME_DATABASE_ROLE, ENV_SCHEMA_OWNER_ROLE),
        (
            runtime_role,
            security_owner,
            ENV_RUNTIME_DATABASE_ROLE,
            ENV_SECURITY_SCHEMA_OWNER_ROLE,
        ),
        (runtime_role, migrator, ENV_RUNTIME_DATABASE_ROLE, ENV_MIGRATOR_ROLE),
        (
            content_owner,
            security_owner,
            ENV_SCHEMA_OWNER_ROLE,
            ENV_SECURITY_SCHEMA_OWNER_ROLE,
        ),
        (content_owner, migrator, ENV_SCHEMA_OWNER_ROLE, ENV_MIGRATOR_ROLE),
        (security_owner, migrator, ENV_SECURITY_SCHEMA_OWNER_ROLE, ENV_MIGRATOR_ROLE),
    )
    for left, right, left_name, right_name in pairs:
        if left == right:
            raise RuntimeConfigurationError(
                f"role separation violated: {left_name} must differ from {right_name}"
            )


def load_release_identity(environ: Mapping[str, str]) -> ReleaseIdentity:
    """Parse fail-closed release identity from governed environment names."""
    return ReleaseIdentity(
        application_version=_require(environ, ENV_RELEASE_VERSION),
        git_sha=_parse_git_sha(_require(environ, ENV_GIT_SHA)),
        build_id=_require(environ, ENV_BUILD_ID),
        artifact_digest=_parse_artifact_digest(_require(environ, ENV_ARTIFACT_DIGEST)),
    )


def load_api_runtime_config(environ: Mapping[str, str]) -> ApiRuntimeConfig:
    """Parse fail-closed STAGING/PRODUCTION API runtime configuration.

    Does not connect to the database. Does not load ``.env`` files.
    """
    # Migration credential must never be injected into API runtime env.
    if ENV_MIGRATOR_DATABASE_URL in environ:
        raise RuntimeConfigurationError(
            f"{ENV_MIGRATOR_DATABASE_URL} must not be present in the API runtime environment"
        )

    for name in _REQUIRED_ENV:
        _require(environ, name)

    environment = _parse_environment(_require(environ, ENV_DEPLOYMENT_ENVIRONMENT))
    release = load_release_identity(environ)
    runtime_role = _parse_role(
        ENV_RUNTIME_DATABASE_ROLE, _require(environ, ENV_RUNTIME_DATABASE_ROLE)
    )
    content_owner = _parse_role(
        ENV_SCHEMA_OWNER_ROLE, _require(environ, ENV_SCHEMA_OWNER_ROLE)
    )
    security_owner = _parse_role(
        ENV_SECURITY_SCHEMA_OWNER_ROLE,
        _require(environ, ENV_SECURITY_SCHEMA_OWNER_ROLE),
    )
    migrator = _parse_role(ENV_MIGRATOR_ROLE, _require(environ, ENV_MIGRATOR_ROLE))
    _assert_role_separation(
        runtime_role=runtime_role,
        content_owner=content_owner,
        security_owner=security_owner,
        migrator=migrator,
    )
    runtime_url = _parse_runtime_database_url(
        _require(environ, ENV_RUNTIME_DATABASE_URL),
        expected_role=runtime_role,
    )
    cursor_key = _parse_cursor_signing_key(_require(environ, ENV_CURSOR_SIGNING_KEY_B64))
    retention = _parse_idempotency_retention(
        _require(environ, ENV_IDEMPOTENCY_RETENTION_SECONDS)
    )
    connect_timeout = _parse_connect_timeout(
        _require(environ, ENV_RUNTIME_DATABASE_CONNECT_TIMEOUT_SECONDS)
    )
    try:
        auth = load_auth_runtime_config(environ)
    except AuthConfigurationError as exc:
        raise RuntimeConfigurationError(str(exc)) from exc
    return ApiRuntimeConfig(
        environment=environment,
        release_identity=release,
        runtime_database_url=runtime_url,
        runtime_database_role=runtime_role,
        content_schema_owner_role=content_owner,
        security_schema_owner_role=security_owner,
        migrator_role=migrator,
        cursor_signing_key=cursor_key,
        idempotency_retention=retention,
        runtime_database_connect_timeout_seconds=connect_timeout,
        auth_issuer=auth.issuer,
        auth_audience=auth.audience,
        auth_jwks_uri=auth.jwks_uri,
    )


def load_api_runtime_config_from_process_environment() -> ApiRuntimeConfig:
    """Thin wrapper around :func:`load_api_runtime_config` using ``os.environ``."""
    return load_api_runtime_config(os.environ)

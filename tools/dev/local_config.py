"""Local-only API runtime environment constants.

LOCAL DEVELOPMENT ONLY — NEVER PRODUCTION.
"""

from __future__ import annotations

import base64
import uuid
from collections.abc import Mapping

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

AUTHORIZED_LOCAL_GIT_SHA = "c760fe3b7635b5f00970f0d2547d0e50cea68e62"
LOCAL_ARTIFACT_DIGEST = "sha256:" + ("0" * 64)
LOCAL_CURSOR_SIGNING_KEY = b"aieos-local-f5-cursor-signing-key"
LOCAL_CURSOR_SIGNING_KEY_B64 = base64.b64encode(LOCAL_CURSOR_SIGNING_KEY).decode("ascii")

# Placeholder HTTPS auth contract values — local launcher does not call JWKS.
LOCAL_AUTH_ISSUER = "https://local-dev.aieos.invalid/"
LOCAL_AUTH_AUDIENCE = "aieos-api"
LOCAL_AUTH_JWKS_URI = "https://local-dev.aieos.invalid/.well-known/jwks.json"

API_BIND_HOST = "127.0.0.1"
API_BIND_PORT = 8080


def build_local_api_environ() -> dict[str, str]:
    """Return safe local-development process environment for ApiRuntimeConfig."""
    runtime_url = (
        f"postgresql+psycopg://{RUNTIME_USER}:{DB_PASSWORD}"
        f"@{HOST}:{HOST_PORT}/aieos"
    )
    return {
        "AIEOS_DEPLOYMENT_ENVIRONMENT": "STAGING",
        "AIEOS_RELEASE_VERSION": "0.1.0",
        "AIEOS_GIT_SHA": AUTHORIZED_LOCAL_GIT_SHA,
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
        # Mutations remain disabled unless explicitly activated locally.
        "AIEOS_API_MUTATION_ACTIVATION": "DISABLED",
    }


def apply_local_api_environ(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Merge local API environment into os.environ and return the merged mapping."""
    import os

    merged = dict(os.environ)
    merged.update(build_local_api_environ())
    if environ is not None:
        merged.update(environ)
    for key, value in build_local_api_environ().items():
        os.environ[key] = value
    return merged

"""Immutable production/staging runtime identity and API configuration models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum


class DeploymentEnvironment(StrEnum):
    """Environments governed by the production configuration package."""

    STAGING = "STAGING"
    PRODUCTION = "PRODUCTION"


class WorkloadKind(StrEnum):
    """Governed application workload kinds (ADR-AIEOS-029).

    MIGRATOR is intentionally absent — it is release/schema authority, not a
    normal application workload. PED-I01 composes API only.
    """

    API = "API"
    EVENT_DISPATCHER = "EVENT_DISPATCHER"
    WORKFLOW_DISPATCHER = "WORKFLOW_DISPATCHER"
    TEMPORAL_WORKER = "TEMPORAL_WORKER"
    CONTENT_MIGRATION = "CONTENT_MIGRATION"


@dataclass(frozen=True, slots=True)
class ReleaseIdentity:
    """Immutable release/build identity. No short Git SHA."""

    application_version: str
    git_sha: str
    build_id: str
    artifact_digest: str


@dataclass(frozen=True, slots=True)
class ApiRuntimeConfig:
    """Immutable STAGING/PRODUCTION API runtime configuration.

    Secret fields are redacted from ``repr`` / ``str``. Do not log this object
    via default formatting if additional secret fields are added later.
    """

    environment: DeploymentEnvironment
    release_identity: ReleaseIdentity
    runtime_database_url: str
    runtime_database_role: str
    content_schema_owner_role: str
    security_schema_owner_role: str
    migrator_role: str
    cursor_signing_key: bytes
    idempotency_retention: timedelta
    runtime_database_connect_timeout_seconds: int
    auth_issuer: str
    auth_audience: str
    auth_jwks_uri: str

    def __repr__(self) -> str:
        return (
            "ApiRuntimeConfig("
            f"environment={self.environment!r}, "
            f"release_identity={self.release_identity!r}, "
            "runtime_database_url=<redacted>, "
            f"runtime_database_role={self.runtime_database_role!r}, "
            f"content_schema_owner_role={self.content_schema_owner_role!r}, "
            f"security_schema_owner_role={self.security_schema_owner_role!r}, "
            f"migrator_role={self.migrator_role!r}, "
            "cursor_signing_key=<redacted>, "
            f"idempotency_retention={self.idempotency_retention!r}, "
            "runtime_database_connect_timeout_seconds="
            f"{self.runtime_database_connect_timeout_seconds!r}, "
            f"auth_issuer={self.auth_issuer!r}, "
            f"auth_audience={self.auth_audience!r}, "
            f"auth_jwks_uri={self.auth_jwks_uri!r}"
            ")"
        )

    def __str__(self) -> str:
        return self.__repr__()

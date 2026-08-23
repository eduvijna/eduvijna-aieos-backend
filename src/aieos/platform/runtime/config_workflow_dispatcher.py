"""Fail-closed STAGING/PRODUCTION WORKFLOW dispatcher runtime configuration (PED-I12).

Operating cadence/batch values are typed validation bounds only — not a production
operating-value freeze (ADR-AIEOS-045/047).
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy.engine.url import make_url

from aieos.platform.runtime.config import (
    ENV_ARTIFACT_DIGEST,
    ENV_BUILD_ID,
    ENV_DEPLOYMENT_ENVIRONMENT,
    ENV_GIT_SHA,
    ENV_RELEASE_VERSION,
    REQUIRED_RUNTIME_DB_DRIVER,
    load_release_identity,
)
from aieos.platform.runtime.errors import RuntimeConfigurationError
from aieos.platform.runtime.models import DeploymentEnvironment, ReleaseIdentity

# Architecture-frozen Temporal WORKFLOW_DISPATCHER env names (ADR-AIEOS-047).
ENV_WORKFLOW_DISPATCHER_TEMPORAL_TARGET_HOST = (
    "AIEOS_WORKFLOW_DISPATCHER_TEMPORAL_TARGET_HOST"
)
ENV_WORKFLOW_DISPATCHER_TEMPORAL_NAMESPACE = (
    "AIEOS_WORKFLOW_DISPATCHER_TEMPORAL_NAMESPACE"
)
ENV_WORKFLOW_DISPATCHER_TEMPORAL_API_KEY = "AIEOS_WORKFLOW_DISPATCHER_TEMPORAL_API_KEY"
ENV_WORKFLOW_DISPATCHER_TEMPORAL_CONNECT_TIMEOUT_SECONDS = (
    "AIEOS_WORKFLOW_DISPATCHER_TEMPORAL_CONNECT_TIMEOUT_SECONDS"
)

# Worker Temporal env family — never accepted as dispatcher credential fallback.
ENV_WORKER_TEMPORAL_TARGET_HOST = "AIEOS_TEMPORAL_TARGET_HOST"
ENV_WORKER_TEMPORAL_NAMESPACE = "AIEOS_TEMPORAL_NAMESPACE"
ENV_WORKER_TEMPORAL_API_KEY = "AIEOS_TEMPORAL_API_KEY"
ENV_WORKER_TEMPORAL_CONNECT_TIMEOUT_SECONDS = "AIEOS_TEMPORAL_CONNECT_TIMEOUT_SECONDS"

# Source-level DB / daemon operating configuration (not a production value freeze).
ENV_WORKFLOW_DISPATCHER_DATABASE_URL = "AIEOS_WORKFLOW_DISPATCHER_DATABASE_URL"
ENV_WORKFLOW_DISPATCHER_ROLE = "AIEOS_WORKFLOW_DISPATCHER_ROLE"
ENV_WORKFLOW_DISPATCHER_DATABASE_CONNECT_TIMEOUT_SECONDS = (
    "AIEOS_WORKFLOW_DISPATCHER_DATABASE_CONNECT_TIMEOUT_SECONDS"
)
ENV_WORKFLOW_DISPATCHER_POLL_INTERVAL_SECONDS = (
    "AIEOS_WORKFLOW_DISPATCHER_POLL_INTERVAL_SECONDS"
)
ENV_WORKFLOW_DISPATCHER_CANDIDATE_BATCH_SIZE = (
    "AIEOS_WORKFLOW_DISPATCHER_CANDIDATE_BATCH_SIZE"
)
ENV_WORKFLOW_DISPATCHER_MAX_INTENTS_PER_TENANT_PER_PASS = (
    "AIEOS_WORKFLOW_DISPATCHER_MAX_INTENTS_PER_TENANT_PER_PASS"
)
ENV_WORKFLOW_DISPATCHER_CLAIM_LEASE_SECONDS = (
    "AIEOS_WORKFLOW_DISPATCHER_CLAIM_LEASE_SECONDS"
)
ENV_WORKFLOW_DISPATCHER_MAX_ATTEMPTS = "AIEOS_WORKFLOW_DISPATCHER_MAX_ATTEMPTS"
ENV_WORKFLOW_DISPATCHER_RETRY_DELAY_SECONDS = (
    "AIEOS_WORKFLOW_DISPATCHER_RETRY_DELAY_SECONDS"
)
ENV_WORKFLOW_DISPATCHER_RESULT_TIMEOUT_SECONDS = (
    "AIEOS_WORKFLOW_DISPATCHER_RESULT_TIMEOUT_SECONDS"
)
ENV_WORKFLOW_DISPATCHER_START_RECONCILIATION_TIMEOUT_SECONDS = (
    "AIEOS_WORKFLOW_DISPATCHER_START_RECONCILIATION_TIMEOUT_SECONDS"
)
ENV_WORKFLOW_DISPATCHER_SHUTDOWN_GRACE_SECONDS = (
    "AIEOS_WORKFLOW_DISPATCHER_SHUTDOWN_GRACE_SECONDS"
)

# Defensive source validation ceilings — NOT a production operating-value freeze.
MAX_CANDIDATE_BATCH_SIZE = 1000
MAX_INTENTS_PER_TENANT_PER_PASS_CEILING = 1000

_ROLE_NAME = re.compile(r"^[a-z_][a-z0-9_]*$")
_POSITIVE_INT = re.compile(r"[1-9][0-9]*")
_NON_NEGATIVE_INT = re.compile(r"0|[1-9][0-9]*")

_REQUIRED_ENV = (
    ENV_DEPLOYMENT_ENVIRONMENT,
    ENV_RELEASE_VERSION,
    ENV_GIT_SHA,
    ENV_BUILD_ID,
    ENV_ARTIFACT_DIGEST,
    ENV_WORKFLOW_DISPATCHER_DATABASE_URL,
    ENV_WORKFLOW_DISPATCHER_ROLE,
    ENV_WORKFLOW_DISPATCHER_DATABASE_CONNECT_TIMEOUT_SECONDS,
    ENV_WORKFLOW_DISPATCHER_TEMPORAL_TARGET_HOST,
    ENV_WORKFLOW_DISPATCHER_TEMPORAL_NAMESPACE,
    ENV_WORKFLOW_DISPATCHER_TEMPORAL_API_KEY,
    ENV_WORKFLOW_DISPATCHER_TEMPORAL_CONNECT_TIMEOUT_SECONDS,
    ENV_WORKFLOW_DISPATCHER_POLL_INTERVAL_SECONDS,
    ENV_WORKFLOW_DISPATCHER_CANDIDATE_BATCH_SIZE,
    ENV_WORKFLOW_DISPATCHER_MAX_INTENTS_PER_TENANT_PER_PASS,
    ENV_WORKFLOW_DISPATCHER_CLAIM_LEASE_SECONDS,
    ENV_WORKFLOW_DISPATCHER_MAX_ATTEMPTS,
    ENV_WORKFLOW_DISPATCHER_RETRY_DELAY_SECONDS,
    ENV_WORKFLOW_DISPATCHER_RESULT_TIMEOUT_SECONDS,
    ENV_WORKFLOW_DISPATCHER_START_RECONCILIATION_TIMEOUT_SECONDS,
    ENV_WORKFLOW_DISPATCHER_SHUTDOWN_GRACE_SECONDS,
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


def _parse_positive_int(name: str, raw: str) -> int:
    if not _POSITIVE_INT.fullmatch(raw):
        raise RuntimeConfigurationError(f"{name} must be a positive integer")
    return int(raw)


def _parse_non_negative_int(name: str, raw: str) -> int:
    if not _NON_NEGATIVE_INT.fullmatch(raw):
        raise RuntimeConfigurationError(f"{name} must be a non-negative integer")
    return int(raw)


def _parse_database_url(raw: str) -> str:
    try:
        url = make_url(raw)
    except Exception as exc:
        raise RuntimeConfigurationError(
            f"{ENV_WORKFLOW_DISPATCHER_DATABASE_URL} is malformed"
        ) from exc
    driver = f"{url.drivername}"
    if driver != REQUIRED_RUNTIME_DB_DRIVER:
        raise RuntimeConfigurationError(
            f"{ENV_WORKFLOW_DISPATCHER_DATABASE_URL} must use {REQUIRED_RUNTIME_DB_DRIVER}"
        )
    return raw


@dataclass(frozen=True, slots=True)
class WorkflowDispatcherRuntimeConfig:
    """Immutable STAGING/PRODUCTION WORKFLOW dispatcher configuration."""

    environment: DeploymentEnvironment
    release_identity: ReleaseIdentity
    database_url: str
    database_role: str
    database_connect_timeout_seconds: int
    temporal_target_host: str
    temporal_namespace: str
    temporal_api_key: str
    temporal_connect_timeout_seconds: int
    poll_interval_seconds: int
    candidate_batch_size: int
    max_intents_per_tenant_per_pass: int
    claim_lease_seconds: int
    max_attempts: int
    retry_delay_seconds: int
    result_timeout_seconds: int
    start_reconciliation_timeout_seconds: int
    shutdown_grace_seconds: int

    def __repr__(self) -> str:
        return (
            "WorkflowDispatcherRuntimeConfig("
            f"environment={self.environment!r}, "
            f"release_identity={self.release_identity!r}, "
            "database_url=<redacted>, "
            f"database_role={self.database_role!r}, "
            f"database_connect_timeout_seconds={self.database_connect_timeout_seconds!r}, "
            f"temporal_target_host={self.temporal_target_host!r}, "
            f"temporal_namespace={self.temporal_namespace!r}, "
            "temporal_api_key=<redacted>, "
            f"temporal_connect_timeout_seconds={self.temporal_connect_timeout_seconds!r}, "
            f"poll_interval_seconds={self.poll_interval_seconds!r}, "
            f"candidate_batch_size={self.candidate_batch_size!r}, "
            f"max_intents_per_tenant_per_pass={self.max_intents_per_tenant_per_pass!r}, "
            f"claim_lease_seconds={self.claim_lease_seconds!r}, "
            f"max_attempts={self.max_attempts!r}, "
            f"retry_delay_seconds={self.retry_delay_seconds!r}, "
            f"result_timeout_seconds={self.result_timeout_seconds!r}, "
            f"start_reconciliation_timeout_seconds="
            f"{self.start_reconciliation_timeout_seconds!r}, "
            f"shutdown_grace_seconds={self.shutdown_grace_seconds!r}"
            ")"
        )

    def __str__(self) -> str:
        return self.__repr__()


def load_workflow_dispatcher_runtime_config(
    environ: Mapping[str, str],
) -> WorkflowDispatcherRuntimeConfig:
    """Parse fail-closed WORKFLOW dispatcher configuration. No .env loading."""
    for name in _REQUIRED_ENV:
        _require(environ, name)

    environment = _parse_environment(_require(environ, ENV_DEPLOYMENT_ENVIRONMENT))
    release = load_release_identity(environ)

    batch = _parse_positive_int(
        ENV_WORKFLOW_DISPATCHER_CANDIDATE_BATCH_SIZE,
        _require(environ, ENV_WORKFLOW_DISPATCHER_CANDIDATE_BATCH_SIZE),
    )
    if batch > MAX_CANDIDATE_BATCH_SIZE:
        raise RuntimeConfigurationError(
            f"{ENV_WORKFLOW_DISPATCHER_CANDIDATE_BATCH_SIZE} must be <= "
            f"{MAX_CANDIDATE_BATCH_SIZE}"
        )

    max_per_tenant = _parse_positive_int(
        ENV_WORKFLOW_DISPATCHER_MAX_INTENTS_PER_TENANT_PER_PASS,
        _require(environ, ENV_WORKFLOW_DISPATCHER_MAX_INTENTS_PER_TENANT_PER_PASS),
    )
    if max_per_tenant > MAX_INTENTS_PER_TENANT_PER_PASS_CEILING:
        raise RuntimeConfigurationError(
            f"{ENV_WORKFLOW_DISPATCHER_MAX_INTENTS_PER_TENANT_PER_PASS} must be <= "
            f"{MAX_INTENTS_PER_TENANT_PER_PASS_CEILING}"
        )

    return WorkflowDispatcherRuntimeConfig(
        environment=environment,
        release_identity=release,
        database_url=_parse_database_url(
            _require(environ, ENV_WORKFLOW_DISPATCHER_DATABASE_URL)
        ),
        database_role=_parse_role(
            ENV_WORKFLOW_DISPATCHER_ROLE,
            _require(environ, ENV_WORKFLOW_DISPATCHER_ROLE),
        ),
        database_connect_timeout_seconds=_parse_positive_int(
            ENV_WORKFLOW_DISPATCHER_DATABASE_CONNECT_TIMEOUT_SECONDS,
            _require(environ, ENV_WORKFLOW_DISPATCHER_DATABASE_CONNECT_TIMEOUT_SECONDS),
        ),
        temporal_target_host=_require(
            environ, ENV_WORKFLOW_DISPATCHER_TEMPORAL_TARGET_HOST
        ),
        temporal_namespace=_require(
            environ, ENV_WORKFLOW_DISPATCHER_TEMPORAL_NAMESPACE
        ),
        temporal_api_key=_require(environ, ENV_WORKFLOW_DISPATCHER_TEMPORAL_API_KEY),
        temporal_connect_timeout_seconds=_parse_positive_int(
            ENV_WORKFLOW_DISPATCHER_TEMPORAL_CONNECT_TIMEOUT_SECONDS,
            _require(environ, ENV_WORKFLOW_DISPATCHER_TEMPORAL_CONNECT_TIMEOUT_SECONDS),
        ),
        poll_interval_seconds=_parse_positive_int(
            ENV_WORKFLOW_DISPATCHER_POLL_INTERVAL_SECONDS,
            _require(environ, ENV_WORKFLOW_DISPATCHER_POLL_INTERVAL_SECONDS),
        ),
        candidate_batch_size=batch,
        max_intents_per_tenant_per_pass=max_per_tenant,
        claim_lease_seconds=_parse_positive_int(
            ENV_WORKFLOW_DISPATCHER_CLAIM_LEASE_SECONDS,
            _require(environ, ENV_WORKFLOW_DISPATCHER_CLAIM_LEASE_SECONDS),
        ),
        max_attempts=_parse_positive_int(
            ENV_WORKFLOW_DISPATCHER_MAX_ATTEMPTS,
            _require(environ, ENV_WORKFLOW_DISPATCHER_MAX_ATTEMPTS),
        ),
        retry_delay_seconds=_parse_non_negative_int(
            ENV_WORKFLOW_DISPATCHER_RETRY_DELAY_SECONDS,
            _require(environ, ENV_WORKFLOW_DISPATCHER_RETRY_DELAY_SECONDS),
        ),
        result_timeout_seconds=_parse_positive_int(
            ENV_WORKFLOW_DISPATCHER_RESULT_TIMEOUT_SECONDS,
            _require(environ, ENV_WORKFLOW_DISPATCHER_RESULT_TIMEOUT_SECONDS),
        ),
        start_reconciliation_timeout_seconds=_parse_positive_int(
            ENV_WORKFLOW_DISPATCHER_START_RECONCILIATION_TIMEOUT_SECONDS,
            _require(
                environ, ENV_WORKFLOW_DISPATCHER_START_RECONCILIATION_TIMEOUT_SECONDS
            ),
        ),
        shutdown_grace_seconds=_parse_positive_int(
            ENV_WORKFLOW_DISPATCHER_SHUTDOWN_GRACE_SECONDS,
            _require(environ, ENV_WORKFLOW_DISPATCHER_SHUTDOWN_GRACE_SECONDS),
        ),
    )


def load_workflow_dispatcher_runtime_config_from_process_environment() -> (
    WorkflowDispatcherRuntimeConfig
):
    return load_workflow_dispatcher_runtime_config(os.environ)

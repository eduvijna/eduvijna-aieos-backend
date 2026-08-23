"""Fail-closed STAGING/PRODUCTION EVENT dispatcher runtime configuration (PED-I11).

Operating cadence/batch values are typed validation bounds only — not a production
operating-value freeze (ADR-AIEOS-045).
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlparse

from sqlalchemy.engine.url import make_url

from aieos.platform.events.constants import PRODUCTION_EVENT_STREAM_NAME
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

ENV_EVENT_DISPATCHER_DATABASE_URL = "AIEOS_EVENT_DISPATCHER_DATABASE_URL"
ENV_EVENT_DISPATCHER_ROLE = "AIEOS_EVENT_DISPATCHER_ROLE"
ENV_EVENT_DISPATCHER_DATABASE_CONNECT_TIMEOUT_SECONDS = (
    "AIEOS_EVENT_DISPATCHER_DATABASE_CONNECT_TIMEOUT_SECONDS"
)
ENV_EVENT_DISPATCHER_NATS_URL = "AIEOS_EVENT_DISPATCHER_NATS_URL"
ENV_EVENT_DISPATCHER_NATS_CREDENTIALS = "AIEOS_EVENT_DISPATCHER_NATS_CREDENTIALS"
ENV_EVENT_DISPATCHER_NATS_CREDENTIALS_FILE = (
    "AIEOS_EVENT_DISPATCHER_NATS_CREDENTIALS_FILE"
)
ENV_EVENT_DISPATCHER_NATS_CONNECT_TIMEOUT_SECONDS = (
    "AIEOS_EVENT_DISPATCHER_NATS_CONNECT_TIMEOUT_SECONDS"
)
ENV_EVENT_DISPATCHER_NATS_CA_BUNDLE_PATH = "AIEOS_EVENT_DISPATCHER_NATS_CA_BUNDLE_PATH"
ENV_EVENT_DISPATCHER_POLL_INTERVAL_SECONDS = (
    "AIEOS_EVENT_DISPATCHER_POLL_INTERVAL_SECONDS"
)
ENV_EVENT_DISPATCHER_CANDIDATE_BATCH_SIZE = (
    "AIEOS_EVENT_DISPATCHER_CANDIDATE_BATCH_SIZE"
)
ENV_EVENT_DISPATCHER_MAX_MESSAGES_PER_TENANT_PER_PASS = (
    "AIEOS_EVENT_DISPATCHER_MAX_MESSAGES_PER_TENANT_PER_PASS"
)
ENV_EVENT_DISPATCHER_CLAIM_LEASE_SECONDS = "AIEOS_EVENT_DISPATCHER_CLAIM_LEASE_SECONDS"
ENV_EVENT_DISPATCHER_MAX_ATTEMPTS = "AIEOS_EVENT_DISPATCHER_MAX_ATTEMPTS"
ENV_EVENT_DISPATCHER_RETRY_DELAY_SECONDS = "AIEOS_EVENT_DISPATCHER_RETRY_DELAY_SECONDS"
ENV_EVENT_DISPATCHER_PUBLISH_TIMEOUT_SECONDS = (
    "AIEOS_EVENT_DISPATCHER_PUBLISH_TIMEOUT_SECONDS"
)
ENV_EVENT_DISPATCHER_SHUTDOWN_GRACE_SECONDS = (
    "AIEOS_EVENT_DISPATCHER_SHUTDOWN_GRACE_SECONDS"
)

# Defensive source validation ceilings — NOT a production operating-value freeze.
MAX_CANDIDATE_BATCH_SIZE = 1000
MAX_MESSAGES_PER_TENANT_PER_PASS_CEILING = 1000

_ROLE_NAME = re.compile(r"^[a-z_][a-z0-9_]*$")
_POSITIVE_INT = re.compile(r"[1-9][0-9]*")
_NON_NEGATIVE_INT = re.compile(r"0|[1-9][0-9]*")

_REQUIRED_ENV = (
    ENV_DEPLOYMENT_ENVIRONMENT,
    ENV_RELEASE_VERSION,
    ENV_GIT_SHA,
    ENV_BUILD_ID,
    ENV_ARTIFACT_DIGEST,
    ENV_EVENT_DISPATCHER_DATABASE_URL,
    ENV_EVENT_DISPATCHER_ROLE,
    ENV_EVENT_DISPATCHER_DATABASE_CONNECT_TIMEOUT_SECONDS,
    ENV_EVENT_DISPATCHER_NATS_URL,
    ENV_EVENT_DISPATCHER_NATS_CREDENTIALS,
    ENV_EVENT_DISPATCHER_NATS_CONNECT_TIMEOUT_SECONDS,
    ENV_EVENT_DISPATCHER_POLL_INTERVAL_SECONDS,
    ENV_EVENT_DISPATCHER_CANDIDATE_BATCH_SIZE,
    ENV_EVENT_DISPATCHER_MAX_MESSAGES_PER_TENANT_PER_PASS,
    ENV_EVENT_DISPATCHER_CLAIM_LEASE_SECONDS,
    ENV_EVENT_DISPATCHER_MAX_ATTEMPTS,
    ENV_EVENT_DISPATCHER_RETRY_DELAY_SECONDS,
    ENV_EVENT_DISPATCHER_PUBLISH_TIMEOUT_SECONDS,
    ENV_EVENT_DISPATCHER_SHUTDOWN_GRACE_SECONDS,
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
            f"{ENV_EVENT_DISPATCHER_DATABASE_URL} is malformed"
        ) from exc
    driver = f"{url.drivername}"
    if driver != REQUIRED_RUNTIME_DB_DRIVER:
        raise RuntimeConfigurationError(
            f"{ENV_EVENT_DISPATCHER_DATABASE_URL} must use {REQUIRED_RUNTIME_DB_DRIVER}"
        )
    return raw


def _parse_nats_url(raw: str, environment: DeploymentEnvironment) -> str:
    parsed = urlparse(raw)
    if parsed.username or parsed.password:
        raise RuntimeConfigurationError(
            f"{ENV_EVENT_DISPATCHER_NATS_URL} must not embed username/password"
        )
    scheme = (parsed.scheme or "").lower()
    if environment in (DeploymentEnvironment.STAGING, DeploymentEnvironment.PRODUCTION):
        if scheme != "tls":
            raise RuntimeConfigurationError(
                f"{ENV_EVENT_DISPATCHER_NATS_URL} must use tls:// in "
                f"{environment.value}"
            )
    elif scheme in {"nats", "ws", "http", "https", ""}:
        # NON_PRODUCTION not used by this loader; STAGING/PRODUCTION only.
        raise RuntimeConfigurationError(
            f"{ENV_EVENT_DISPATCHER_NATS_URL} scheme is not permitted"
        )
    if "://" not in raw:
        raise RuntimeConfigurationError(
            f"{ENV_EVENT_DISPATCHER_NATS_URL} must be an absolute URL"
        )
    return raw


@dataclass(frozen=True, slots=True)
class EventDispatcherRuntimeConfig:
    """Immutable STAGING/PRODUCTION EVENT dispatcher configuration."""

    environment: DeploymentEnvironment
    release_identity: ReleaseIdentity
    database_url: str
    database_role: str
    database_connect_timeout_seconds: int
    nats_url: str
    nats_credentials: str
    nats_connect_timeout_seconds: int
    nats_ca_bundle_path: str | None
    poll_interval_seconds: int
    candidate_batch_size: int
    max_messages_per_tenant_per_pass: int
    claim_lease_seconds: int
    max_attempts: int
    retry_delay_seconds: int
    publish_timeout_seconds: int
    shutdown_grace_seconds: int
    expected_stream: str = PRODUCTION_EVENT_STREAM_NAME

    def __repr__(self) -> str:
        return (
            "EventDispatcherRuntimeConfig("
            f"environment={self.environment!r}, "
            f"release_identity={self.release_identity!r}, "
            "database_url=<redacted>, "
            f"database_role={self.database_role!r}, "
            f"database_connect_timeout_seconds={self.database_connect_timeout_seconds!r}, "
            f"nats_url={self.nats_url!r}, "
            "nats_credentials=<redacted>, "
            f"nats_connect_timeout_seconds={self.nats_connect_timeout_seconds!r}, "
            f"nats_ca_bundle_path={self.nats_ca_bundle_path!r}, "
            f"poll_interval_seconds={self.poll_interval_seconds!r}, "
            f"candidate_batch_size={self.candidate_batch_size!r}, "
            f"max_messages_per_tenant_per_pass={self.max_messages_per_tenant_per_pass!r}, "
            f"claim_lease_seconds={self.claim_lease_seconds!r}, "
            f"max_attempts={self.max_attempts!r}, "
            f"retry_delay_seconds={self.retry_delay_seconds!r}, "
            f"publish_timeout_seconds={self.publish_timeout_seconds!r}, "
            f"shutdown_grace_seconds={self.shutdown_grace_seconds!r}, "
            f"expected_stream={self.expected_stream!r}"
            ")"
        )

    def __str__(self) -> str:
        return self.__repr__()


def load_event_dispatcher_runtime_config(
    environ: Mapping[str, str],
) -> EventDispatcherRuntimeConfig:
    """Parse fail-closed EVENT dispatcher configuration. No .env loading."""
    file_cred = environ.get(ENV_EVENT_DISPATCHER_NATS_CREDENTIALS_FILE)
    if file_cred is not None and str(file_cred).strip() != "":
        raise RuntimeConfigurationError(
            f"{ENV_EVENT_DISPATCHER_NATS_CREDENTIALS_FILE} is not production authority "
            "and must not be set"
        )

    for name in _REQUIRED_ENV:
        _require(environ, name)

    environment = _parse_environment(_require(environ, ENV_DEPLOYMENT_ENVIRONMENT))
    release = load_release_identity(environ)

    batch = _parse_positive_int(
        ENV_EVENT_DISPATCHER_CANDIDATE_BATCH_SIZE,
        _require(environ, ENV_EVENT_DISPATCHER_CANDIDATE_BATCH_SIZE),
    )
    if batch > MAX_CANDIDATE_BATCH_SIZE:
        raise RuntimeConfigurationError(
            f"{ENV_EVENT_DISPATCHER_CANDIDATE_BATCH_SIZE} must be <= {MAX_CANDIDATE_BATCH_SIZE}"
        )

    max_per_tenant = _parse_positive_int(
        ENV_EVENT_DISPATCHER_MAX_MESSAGES_PER_TENANT_PER_PASS,
        _require(environ, ENV_EVENT_DISPATCHER_MAX_MESSAGES_PER_TENANT_PER_PASS),
    )
    if max_per_tenant > MAX_MESSAGES_PER_TENANT_PER_PASS_CEILING:
        raise RuntimeConfigurationError(
            f"{ENV_EVENT_DISPATCHER_MAX_MESSAGES_PER_TENANT_PER_PASS} must be <= "
            f"{MAX_MESSAGES_PER_TENANT_PER_PASS_CEILING}"
        )

    ca_raw = environ.get(ENV_EVENT_DISPATCHER_NATS_CA_BUNDLE_PATH)
    ca_path = ca_raw.strip() if ca_raw is not None and ca_raw.strip() != "" else None

    return EventDispatcherRuntimeConfig(
        environment=environment,
        release_identity=release,
        database_url=_parse_database_url(
            _require(environ, ENV_EVENT_DISPATCHER_DATABASE_URL)
        ),
        database_role=_parse_role(
            ENV_EVENT_DISPATCHER_ROLE,
            _require(environ, ENV_EVENT_DISPATCHER_ROLE),
        ),
        database_connect_timeout_seconds=_parse_positive_int(
            ENV_EVENT_DISPATCHER_DATABASE_CONNECT_TIMEOUT_SECONDS,
            _require(environ, ENV_EVENT_DISPATCHER_DATABASE_CONNECT_TIMEOUT_SECONDS),
        ),
        nats_url=_parse_nats_url(
            _require(environ, ENV_EVENT_DISPATCHER_NATS_URL),
            environment,
        ),
        nats_credentials=_require(environ, ENV_EVENT_DISPATCHER_NATS_CREDENTIALS),
        nats_connect_timeout_seconds=_parse_positive_int(
            ENV_EVENT_DISPATCHER_NATS_CONNECT_TIMEOUT_SECONDS,
            _require(environ, ENV_EVENT_DISPATCHER_NATS_CONNECT_TIMEOUT_SECONDS),
        ),
        nats_ca_bundle_path=ca_path,
        poll_interval_seconds=_parse_positive_int(
            ENV_EVENT_DISPATCHER_POLL_INTERVAL_SECONDS,
            _require(environ, ENV_EVENT_DISPATCHER_POLL_INTERVAL_SECONDS),
        ),
        candidate_batch_size=batch,
        max_messages_per_tenant_per_pass=max_per_tenant,
        claim_lease_seconds=_parse_positive_int(
            ENV_EVENT_DISPATCHER_CLAIM_LEASE_SECONDS,
            _require(environ, ENV_EVENT_DISPATCHER_CLAIM_LEASE_SECONDS),
        ),
        max_attempts=_parse_positive_int(
            ENV_EVENT_DISPATCHER_MAX_ATTEMPTS,
            _require(environ, ENV_EVENT_DISPATCHER_MAX_ATTEMPTS),
        ),
        retry_delay_seconds=_parse_non_negative_int(
            ENV_EVENT_DISPATCHER_RETRY_DELAY_SECONDS,
            _require(environ, ENV_EVENT_DISPATCHER_RETRY_DELAY_SECONDS),
        ),
        publish_timeout_seconds=_parse_positive_int(
            ENV_EVENT_DISPATCHER_PUBLISH_TIMEOUT_SECONDS,
            _require(environ, ENV_EVENT_DISPATCHER_PUBLISH_TIMEOUT_SECONDS),
        ),
        shutdown_grace_seconds=_parse_positive_int(
            ENV_EVENT_DISPATCHER_SHUTDOWN_GRACE_SECONDS,
            _require(environ, ENV_EVENT_DISPATCHER_SHUTDOWN_GRACE_SECONDS),
        ),
    )


def load_event_dispatcher_runtime_config_from_process_environment() -> (
    EventDispatcherRuntimeConfig
):
    return load_event_dispatcher_runtime_config(os.environ)

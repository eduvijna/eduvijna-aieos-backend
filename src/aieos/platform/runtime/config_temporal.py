"""Fail-closed STAGING/PRODUCTION Temporal worker runtime configuration."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass

from aieos.platform.runtime.config import (
    ENV_ARTIFACT_DIGEST,
    ENV_BUILD_ID,
    ENV_DEPLOYMENT_ENVIRONMENT,
    ENV_GIT_SHA,
    ENV_RELEASE_VERSION,
    load_release_identity,
)
from aieos.platform.runtime.errors import RuntimeConfigurationError
from aieos.platform.runtime.models import DeploymentEnvironment, ReleaseIdentity
from aieos.platform.workflows.constants import CONTENT_REVIEW_TASK_QUEUE

ENV_TEMPORAL_TARGET_HOST = "AIEOS_TEMPORAL_TARGET_HOST"
ENV_TEMPORAL_NAMESPACE = "AIEOS_TEMPORAL_NAMESPACE"
ENV_TEMPORAL_API_KEY = "AIEOS_TEMPORAL_API_KEY"
ENV_TEMPORAL_CONNECT_TIMEOUT_SECONDS = "AIEOS_TEMPORAL_CONNECT_TIMEOUT_SECONDS"
ENV_TEMPORAL_SHUTDOWN_GRACE_SECONDS = "AIEOS_TEMPORAL_SHUTDOWN_GRACE_SECONDS"

_REQUIRED_ENV = (
    ENV_DEPLOYMENT_ENVIRONMENT,
    ENV_RELEASE_VERSION,
    ENV_GIT_SHA,
    ENV_BUILD_ID,
    ENV_ARTIFACT_DIGEST,
    ENV_TEMPORAL_TARGET_HOST,
    ENV_TEMPORAL_NAMESPACE,
    ENV_TEMPORAL_API_KEY,
    ENV_TEMPORAL_CONNECT_TIMEOUT_SECONDS,
    ENV_TEMPORAL_SHUTDOWN_GRACE_SECONDS,
)

_POSITIVE_INT = re.compile(r"[1-9][0-9]*")


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


def _parse_positive_int(name: str, raw: str) -> int:
    if not _POSITIVE_INT.fullmatch(raw):
        raise RuntimeConfigurationError(f"{name} must be a positive integer")
    return int(raw)


@dataclass(frozen=True, slots=True)
class TemporalWorkerRuntimeConfig:
    """Immutable STAGING/PRODUCTION Temporal worker configuration."""

    environment: DeploymentEnvironment
    release_identity: ReleaseIdentity
    target_host: str
    namespace: str
    api_key: str
    connect_timeout_seconds: int
    shutdown_grace_seconds: int
    task_queue: str = CONTENT_REVIEW_TASK_QUEUE

    def __repr__(self) -> str:
        return (
            "TemporalWorkerRuntimeConfig("
            f"environment={self.environment!r}, "
            f"release_identity={self.release_identity!r}, "
            f"target_host={self.target_host!r}, "
            f"namespace={self.namespace!r}, "
            "api_key=<redacted>, "
            f"connect_timeout_seconds={self.connect_timeout_seconds!r}, "
            f"shutdown_grace_seconds={self.shutdown_grace_seconds!r}, "
            f"task_queue={self.task_queue!r}"
            ")"
        )

    def __str__(self) -> str:
        return self.__repr__()


def load_temporal_worker_runtime_config(
    environ: Mapping[str, str],
) -> TemporalWorkerRuntimeConfig:
    """Parse fail-closed Temporal worker configuration.

    Does not connect to Temporal. Does not load ``.env`` files.
    """
    for name in _REQUIRED_ENV:
        _require(environ, name)

    environment = _parse_environment(_require(environ, ENV_DEPLOYMENT_ENVIRONMENT))
    release = load_release_identity(environ)
    connect_timeout = _parse_positive_int(
        ENV_TEMPORAL_CONNECT_TIMEOUT_SECONDS,
        _require(environ, ENV_TEMPORAL_CONNECT_TIMEOUT_SECONDS),
    )
    shutdown_grace = _parse_positive_int(
        ENV_TEMPORAL_SHUTDOWN_GRACE_SECONDS,
        _require(environ, ENV_TEMPORAL_SHUTDOWN_GRACE_SECONDS),
    )
    return TemporalWorkerRuntimeConfig(
        environment=environment,
        release_identity=release,
        target_host=_require(environ, ENV_TEMPORAL_TARGET_HOST),
        namespace=_require(environ, ENV_TEMPORAL_NAMESPACE),
        api_key=_require(environ, ENV_TEMPORAL_API_KEY),
        connect_timeout_seconds=connect_timeout,
        shutdown_grace_seconds=shutdown_grace,
    )


def load_temporal_worker_runtime_config_from_process_environment() -> (
    TemporalWorkerRuntimeConfig
):
    return load_temporal_worker_runtime_config(os.environ)

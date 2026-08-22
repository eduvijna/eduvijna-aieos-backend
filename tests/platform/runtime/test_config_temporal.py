"""Temporal worker runtime configuration tests."""

from __future__ import annotations

import pytest

from aieos.platform.runtime.config import (
    ENV_ARTIFACT_DIGEST,
    ENV_BUILD_ID,
    ENV_DEPLOYMENT_ENVIRONMENT,
    ENV_GIT_SHA,
    ENV_RELEASE_VERSION,
)
from aieos.platform.runtime.config_temporal import (
    ENV_TEMPORAL_API_KEY,
    ENV_TEMPORAL_CONNECT_TIMEOUT_SECONDS,
    ENV_TEMPORAL_NAMESPACE,
    ENV_TEMPORAL_SHUTDOWN_GRACE_SECONDS,
    ENV_TEMPORAL_TARGET_HOST,
    TemporalWorkerRuntimeConfig,
    load_temporal_worker_runtime_config,
)
from aieos.platform.runtime.errors import RuntimeConfigurationError
from aieos.platform.workflows.constants import CONTENT_REVIEW_TASK_QUEUE

pytestmark = pytest.mark.ped_i01

VALID_GIT_SHA = "c" * 40
VALID_DIGEST = "sha256:" + ("d" * 64)
SECRET_API_KEY = "TEMPORAL_CLOUD_SECRET_KEY"


def _valid_environ(**overrides: str) -> dict[str, str]:
    env = {
        ENV_DEPLOYMENT_ENVIRONMENT: "PRODUCTION",
        ENV_RELEASE_VERSION: "0.1.0",
        ENV_GIT_SHA: VALID_GIT_SHA,
        ENV_BUILD_ID: "build-temporal",
        ENV_ARTIFACT_DIGEST: VALID_DIGEST,
        ENV_TEMPORAL_TARGET_HOST: "namespace.tmprl.cloud:7233",
        ENV_TEMPORAL_NAMESPACE: "aieos-production",
        ENV_TEMPORAL_API_KEY: SECRET_API_KEY,
        ENV_TEMPORAL_CONNECT_TIMEOUT_SECONDS: "10",
        ENV_TEMPORAL_SHUTDOWN_GRACE_SECONDS: "30",
    }
    env.update(overrides)
    return env


class TestTemporalConfigLoader:
    def test_complete_valid_config_loads(self) -> None:
        config = load_temporal_worker_runtime_config(_valid_environ())
        assert isinstance(config, TemporalWorkerRuntimeConfig)
        assert config.task_queue == CONTENT_REVIEW_TASK_QUEUE
        assert config.api_key == SECRET_API_KEY

    @pytest.mark.parametrize(
        "missing",
        (
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
        ),
    )
    def test_missing_required_field_fail_closed(self, missing: str) -> None:
        env = _valid_environ()
        del env[missing]
        with pytest.raises(RuntimeConfigurationError) as excinfo:
            load_temporal_worker_runtime_config(env)
        assert missing in str(excinfo.value)

    def test_api_key_redacted_from_repr(self) -> None:
        config = load_temporal_worker_runtime_config(_valid_environ())
        text = repr(config) + str(config)
        assert SECRET_API_KEY not in text
        assert "<redacted>" in repr(config)

    def test_malformed_git_sha_fail_closed(self) -> None:
        with pytest.raises(RuntimeConfigurationError) as excinfo:
            load_temporal_worker_runtime_config(
                _valid_environ(**{ENV_GIT_SHA: "not-a-sha"})
            )
        assert ENV_GIT_SHA in str(excinfo.value)

    @pytest.mark.parametrize("environment", ["STAGING", "PRODUCTION"])
    def test_staging_and_production_allowed(self, environment: str) -> None:
        config = load_temporal_worker_runtime_config(
            _valid_environ(**{ENV_DEPLOYMENT_ENVIRONMENT: environment})
        )
        assert config.environment.value == environment

    def test_no_database_or_nats_fields_required(self) -> None:
        env = _valid_environ()
        for forbidden in (
            "AIEOS_RUNTIME_DATABASE_URL",
            "AIEOS_DATABASE_URL",
            "AIEOS_CURSOR_SIGNING_KEY_B64",
            "NATS_URL",
        ):
            assert forbidden not in env

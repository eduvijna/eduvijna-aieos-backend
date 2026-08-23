"""PED-I12 WORKFLOW dispatcher runtime configuration tests."""

from __future__ import annotations

import pytest

from aieos.platform.runtime.config_workflow_dispatcher import (
    ENV_WORKER_TEMPORAL_API_KEY,
    ENV_WORKER_TEMPORAL_NAMESPACE,
    ENV_WORKER_TEMPORAL_TARGET_HOST,
    ENV_WORKFLOW_DISPATCHER_TEMPORAL_API_KEY,
    ENV_WORKFLOW_DISPATCHER_TEMPORAL_CONNECT_TIMEOUT_SECONDS,
    ENV_WORKFLOW_DISPATCHER_TEMPORAL_NAMESPACE,
    ENV_WORKFLOW_DISPATCHER_TEMPORAL_TARGET_HOST,
    load_workflow_dispatcher_runtime_config,
)
from aieos.platform.runtime.errors import RuntimeConfigurationError

pytestmark = pytest.mark.ped_i12

_SECRET_API_KEY = "sk_live_DISPATCHER_SECRET_KEY_MATERIAL_XYZ"
_SECRET_DB = "postgresql+psycopg://disp_user:s3cretPass@localhost:5432/aieos"

_BASE = {
    "AIEOS_DEPLOYMENT_ENVIRONMENT": "PRODUCTION",
    "AIEOS_RELEASE_VERSION": "0.1.0",
    "AIEOS_GIT_SHA": "a" * 40,
    "AIEOS_BUILD_ID": "build-1",
    "AIEOS_ARTIFACT_DIGEST": "sha256:" + ("b" * 64),
    "AIEOS_WORKFLOW_DISPATCHER_DATABASE_URL": _SECRET_DB,
    "AIEOS_WORKFLOW_DISPATCHER_ROLE": "aieos_workflow_dispatcher",
    "AIEOS_WORKFLOW_DISPATCHER_DATABASE_CONNECT_TIMEOUT_SECONDS": "5",
    "AIEOS_WORKFLOW_DISPATCHER_TEMPORAL_TARGET_HOST": "temporal.example:7233",
    "AIEOS_WORKFLOW_DISPATCHER_TEMPORAL_NAMESPACE": "aieos-staging.example",
    "AIEOS_WORKFLOW_DISPATCHER_TEMPORAL_API_KEY": _SECRET_API_KEY,
    "AIEOS_WORKFLOW_DISPATCHER_TEMPORAL_CONNECT_TIMEOUT_SECONDS": "5",
    "AIEOS_WORKFLOW_DISPATCHER_POLL_INTERVAL_SECONDS": "2",
    "AIEOS_WORKFLOW_DISPATCHER_CANDIDATE_BATCH_SIZE": "10",
    "AIEOS_WORKFLOW_DISPATCHER_MAX_INTENTS_PER_TENANT_PER_PASS": "3",
    "AIEOS_WORKFLOW_DISPATCHER_CLAIM_LEASE_SECONDS": "30",
    "AIEOS_WORKFLOW_DISPATCHER_MAX_ATTEMPTS": "5",
    "AIEOS_WORKFLOW_DISPATCHER_RETRY_DELAY_SECONDS": "1",
    "AIEOS_WORKFLOW_DISPATCHER_RESULT_TIMEOUT_SECONDS": "30",
    "AIEOS_WORKFLOW_DISPATCHER_START_RECONCILIATION_TIMEOUT_SECONDS": "10",
    "AIEOS_WORKFLOW_DISPATCHER_SHUTDOWN_GRACE_SECONDS": "15",
}


def test_valid_config_loads() -> None:
    cfg = load_workflow_dispatcher_runtime_config(_BASE)
    assert cfg.candidate_batch_size == 10
    assert cfg.temporal_namespace == "aieos-staging.example"
    assert cfg.temporal_api_key == _SECRET_API_KEY
    assert _SECRET_API_KEY not in repr(cfg)
    assert _SECRET_API_KEY not in str(cfg)
    assert "s3cretPass" not in repr(cfg)
    assert "s3cretPass" not in str(cfg)
    assert "<redacted>" in repr(cfg)
    assert "<redacted>" in str(cfg)


def test_architecture_frozen_temporal_env_names() -> None:
    assert ENV_WORKFLOW_DISPATCHER_TEMPORAL_TARGET_HOST == (
        "AIEOS_WORKFLOW_DISPATCHER_TEMPORAL_TARGET_HOST"
    )
    assert ENV_WORKFLOW_DISPATCHER_TEMPORAL_NAMESPACE == (
        "AIEOS_WORKFLOW_DISPATCHER_TEMPORAL_NAMESPACE"
    )
    assert ENV_WORKFLOW_DISPATCHER_TEMPORAL_API_KEY == (
        "AIEOS_WORKFLOW_DISPATCHER_TEMPORAL_API_KEY"
    )
    assert ENV_WORKFLOW_DISPATCHER_TEMPORAL_CONNECT_TIMEOUT_SECONDS == (
        "AIEOS_WORKFLOW_DISPATCHER_TEMPORAL_CONNECT_TIMEOUT_SECONDS"
    )


@pytest.mark.parametrize(
    "missing",
    [
        "AIEOS_WORKFLOW_DISPATCHER_TEMPORAL_TARGET_HOST",
        "AIEOS_WORKFLOW_DISPATCHER_TEMPORAL_NAMESPACE",
        "AIEOS_WORKFLOW_DISPATCHER_TEMPORAL_API_KEY",
        "AIEOS_WORKFLOW_DISPATCHER_TEMPORAL_CONNECT_TIMEOUT_SECONDS",
        "AIEOS_WORKFLOW_DISPATCHER_DATABASE_URL",
        "AIEOS_WORKFLOW_DISPATCHER_POLL_INTERVAL_SECONDS",
        "AIEOS_GIT_SHA",
    ],
)
def test_missing_required_fails(missing: str) -> None:
    env = dict(_BASE)
    del env[missing]
    with pytest.raises(RuntimeConfigurationError):
        load_workflow_dispatcher_runtime_config(env)


def test_worker_temporal_env_not_dispatcher_fallback() -> None:
    env = dict(_BASE)
    del env["AIEOS_WORKFLOW_DISPATCHER_TEMPORAL_API_KEY"]
    del env["AIEOS_WORKFLOW_DISPATCHER_TEMPORAL_TARGET_HOST"]
    del env["AIEOS_WORKFLOW_DISPATCHER_TEMPORAL_NAMESPACE"]
    del env["AIEOS_WORKFLOW_DISPATCHER_TEMPORAL_CONNECT_TIMEOUT_SECONDS"]
    env[ENV_WORKER_TEMPORAL_API_KEY] = "worker-api-key-secret"
    env[ENV_WORKER_TEMPORAL_TARGET_HOST] = "worker.temporal.example:7233"
    env[ENV_WORKER_TEMPORAL_NAMESPACE] = "worker-ns"
    env["AIEOS_TEMPORAL_CONNECT_TIMEOUT_SECONDS"] = "99"
    with pytest.raises(RuntimeConfigurationError):
        load_workflow_dispatcher_runtime_config(env)


def test_blank_api_key_fails() -> None:
    env = dict(_BASE)
    env["AIEOS_WORKFLOW_DISPATCHER_TEMPORAL_API_KEY"] = "  "
    with pytest.raises(RuntimeConfigurationError):
        load_workflow_dispatcher_runtime_config(env)


def test_malformed_database_url_fails() -> None:
    env = dict(_BASE)
    env["AIEOS_WORKFLOW_DISPATCHER_DATABASE_URL"] = "sqlite:///x.db"
    with pytest.raises(RuntimeConfigurationError):
        load_workflow_dispatcher_runtime_config(env)


def test_invalid_db_role_fails() -> None:
    env = dict(_BASE)
    env["AIEOS_WORKFLOW_DISPATCHER_ROLE"] = "Aieos_Workflow"
    with pytest.raises(RuntimeConfigurationError):
        load_workflow_dispatcher_runtime_config(env)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("AIEOS_WORKFLOW_DISPATCHER_CANDIDATE_BATCH_SIZE", "0"),
        ("AIEOS_WORKFLOW_DISPATCHER_CANDIDATE_BATCH_SIZE", "1001"),
        ("AIEOS_WORKFLOW_DISPATCHER_POLL_INTERVAL_SECONDS", "0"),
        ("AIEOS_WORKFLOW_DISPATCHER_MAX_ATTEMPTS", "0"),
        ("AIEOS_WORKFLOW_DISPATCHER_RETRY_DELAY_SECONDS", "-1"),
        ("AIEOS_WORKFLOW_DISPATCHER_MAX_INTENTS_PER_TENANT_PER_PASS", "0"),
        ("AIEOS_WORKFLOW_DISPATCHER_TEMPORAL_CONNECT_TIMEOUT_SECONDS", "0"),
    ],
)
def test_operating_bounds(key: str, value: str) -> None:
    env = dict(_BASE)
    env[key] = value
    with pytest.raises(RuntimeConfigurationError):
        load_workflow_dispatcher_runtime_config(env)


def test_staging_environment_accepted() -> None:
    env = dict(_BASE)
    env["AIEOS_DEPLOYMENT_ENVIRONMENT"] = "STAGING"
    cfg = load_workflow_dispatcher_runtime_config(env)
    assert cfg.environment.value == "STAGING"


def test_invalid_environment_rejected() -> None:
    env = dict(_BASE)
    env["AIEOS_DEPLOYMENT_ENVIRONMENT"] = "DEV"
    with pytest.raises(RuntimeConfigurationError, match="STAGING or PRODUCTION"):
        load_workflow_dispatcher_runtime_config(env)


def test_no_dotenv_loading(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "AIEOS_WORKFLOW_DISPATCHER_TEMPORAL_API_KEY=from-dotenv\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    env = dict(_BASE)
    del env["AIEOS_WORKFLOW_DISPATCHER_TEMPORAL_API_KEY"]
    with pytest.raises(RuntimeConfigurationError):
        load_workflow_dispatcher_runtime_config(env)


def test_import_creates_no_connection() -> None:
    import aieos.platform.runtime.entrypoints.workflow_dispatcher_main as mod

    assert callable(mod.main)

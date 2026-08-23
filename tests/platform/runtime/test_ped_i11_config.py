"""PED-I11 EVENT dispatcher runtime configuration tests."""

from __future__ import annotations

import pytest

from aieos.platform.runtime.config_event_dispatcher import (
    ENV_EVENT_DISPATCHER_NATS_CREDENTIALS_FILE,
    load_event_dispatcher_runtime_config,
)
from aieos.platform.runtime.errors import RuntimeConfigurationError

pytestmark = pytest.mark.ped_i11

_BASE = {
    "AIEOS_DEPLOYMENT_ENVIRONMENT": "PRODUCTION",
    "AIEOS_RELEASE_VERSION": "0.1.0",
    "AIEOS_GIT_SHA": "a" * 40,
    "AIEOS_BUILD_ID": "build-1",
    "AIEOS_ARTIFACT_DIGEST": "sha256:" + ("b" * 64),
    "AIEOS_EVENT_DISPATCHER_DATABASE_URL": "postgresql+psycopg://u:p@localhost:5432/db",
    "AIEOS_EVENT_DISPATCHER_ROLE": "aieos_event_dispatcher",
    "AIEOS_EVENT_DISPATCHER_DATABASE_CONNECT_TIMEOUT_SECONDS": "5",
    "AIEOS_EVENT_DISPATCHER_NATS_URL": "tls://nats.internal.example:4222",
    "AIEOS_EVENT_DISPATCHER_NATS_CREDENTIALS": "dummy-creds-material",
    "AIEOS_EVENT_DISPATCHER_NATS_CONNECT_TIMEOUT_SECONDS": "5",
    "AIEOS_EVENT_DISPATCHER_POLL_INTERVAL_SECONDS": "2",
    "AIEOS_EVENT_DISPATCHER_CANDIDATE_BATCH_SIZE": "10",
    "AIEOS_EVENT_DISPATCHER_MAX_MESSAGES_PER_TENANT_PER_PASS": "3",
    "AIEOS_EVENT_DISPATCHER_CLAIM_LEASE_SECONDS": "30",
    "AIEOS_EVENT_DISPATCHER_MAX_ATTEMPTS": "5",
    "AIEOS_EVENT_DISPATCHER_RETRY_DELAY_SECONDS": "1",
    "AIEOS_EVENT_DISPATCHER_PUBLISH_TIMEOUT_SECONDS": "10",
    "AIEOS_EVENT_DISPATCHER_SHUTDOWN_GRACE_SECONDS": "15",
}


def test_valid_config_loads() -> None:
    cfg = load_event_dispatcher_runtime_config(_BASE)
    assert cfg.candidate_batch_size == 10
    assert cfg.expected_stream == "AIEOS_EVENTS_PROD"
    assert "dummy-creds" not in repr(cfg)
    assert "<redacted>" in repr(cfg)
    assert "<redacted>" in str(cfg)
    assert "postgresql+psycopg://u:p@" not in repr(cfg)


@pytest.mark.parametrize(
    "missing",
    [
        "AIEOS_EVENT_DISPATCHER_NATS_CREDENTIALS",
        "AIEOS_EVENT_DISPATCHER_DATABASE_URL",
        "AIEOS_EVENT_DISPATCHER_POLL_INTERVAL_SECONDS",
    ],
)
def test_missing_required_fails(missing: str) -> None:
    env = dict(_BASE)
    del env[missing]
    with pytest.raises(RuntimeConfigurationError):
        load_event_dispatcher_runtime_config(env)


def test_empty_credentials_fail() -> None:
    env = dict(_BASE)
    env["AIEOS_EVENT_DISPATCHER_NATS_CREDENTIALS"] = "  "
    with pytest.raises(RuntimeConfigurationError):
        load_event_dispatcher_runtime_config(env)


def test_credentials_file_rejected() -> None:
    env = dict(_BASE)
    env[ENV_EVENT_DISPATCHER_NATS_CREDENTIALS_FILE] = "/tmp/x.creds"
    with pytest.raises(RuntimeConfigurationError, match="not production authority"):
        load_event_dispatcher_runtime_config(env)


def test_nats_plaintext_rejected() -> None:
    env = dict(_BASE)
    env["AIEOS_EVENT_DISPATCHER_NATS_URL"] = "nats://127.0.0.1:4222"
    with pytest.raises(RuntimeConfigurationError, match="tls://"):
        load_event_dispatcher_runtime_config(env)


def test_embedded_nats_userinfo_rejected() -> None:
    env = dict(_BASE)
    env["AIEOS_EVENT_DISPATCHER_NATS_URL"] = "tls://user:pass@nats.example:4222"
    with pytest.raises(RuntimeConfigurationError, match="username/password"):
        load_event_dispatcher_runtime_config(env)


def test_malformed_database_url_fails() -> None:
    env = dict(_BASE)
    env["AIEOS_EVENT_DISPATCHER_DATABASE_URL"] = "sqlite:///x.db"
    with pytest.raises(RuntimeConfigurationError):
        load_event_dispatcher_runtime_config(env)


def test_invalid_db_role_fails() -> None:
    env = dict(_BASE)
    env["AIEOS_EVENT_DISPATCHER_ROLE"] = "Aieos_Event"
    with pytest.raises(RuntimeConfigurationError):
        load_event_dispatcher_runtime_config(env)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("AIEOS_EVENT_DISPATCHER_CANDIDATE_BATCH_SIZE", "0"),
        ("AIEOS_EVENT_DISPATCHER_CANDIDATE_BATCH_SIZE", "1001"),
        ("AIEOS_EVENT_DISPATCHER_POLL_INTERVAL_SECONDS", "0"),
        ("AIEOS_EVENT_DISPATCHER_MAX_ATTEMPTS", "0"),
        ("AIEOS_EVENT_DISPATCHER_RETRY_DELAY_SECONDS", "-1"),
        ("AIEOS_EVENT_DISPATCHER_MAX_MESSAGES_PER_TENANT_PER_PASS", "0"),
    ],
)
def test_operating_bounds(key: str, value: str) -> None:
    env = dict(_BASE)
    env[key] = value
    with pytest.raises(RuntimeConfigurationError):
        load_event_dispatcher_runtime_config(env)


def test_import_creates_no_connection() -> None:
    import aieos.platform.runtime.entrypoints.event_dispatcher_main as mod

    assert callable(mod.main)

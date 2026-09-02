"""Focused tests for local F5 developer tooling."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.dbutil import REPO_ROOT

pytestmark = pytest.mark.ped_i01


def test_production_api_main_does_not_import_local_dev_modules() -> None:
    import aieos.platform.runtime.entrypoints.api_main as api_main

    source = Path(api_main.__file__).read_text(encoding="utf-8")
    assert "tools.dev" not in source
    assert "tools/dev" not in source
    assert "run_local_api" not in source
    assert "local_auth" not in source
    assert "compose_local_api" not in source
    assert "aieos.development" not in source


def test_production_config_has_no_local_development_mode() -> None:
    from aieos.platform.runtime.config import DeploymentEnvironment

    values = {member.value for member in DeploymentEnvironment}
    assert values == {"STAGING", "PRODUCTION"}


def test_local_launcher_refuses_non_loopback_bind() -> None:
    from aieos.platform.runtime.errors import RuntimeConfigurationError
    from tools.dev.run_local_api import resolve_bind_host

    with pytest.raises(RuntimeConfigurationError):
        resolve_bind_host("0.0.0.0")


def test_local_launcher_allows_loopback_bind() -> None:
    from tools.dev.run_local_api import resolve_bind_host

    assert resolve_bind_host("127.0.0.1") == "127.0.0.1"
    assert resolve_bind_host("localhost") == "localhost"


def test_local_db_host_must_be_loopback() -> None:
    from tools.dev.postgres_substrate import validate_db_host

    validate_db_host("127.0.0.1")
    validate_db_host("localhost")
    with pytest.raises(ValueError, match="local database host"):
        validate_db_host("10.0.0.5")
    with pytest.raises(ValueError, match="production-like"):
        validate_db_host("prod.cluster-abc.us-east-1.rds.amazonaws.com")


def test_local_db_urls_use_loopback_host() -> None:
    from tools.dev.constants import HOST
    from tools.dev.postgres_substrate import bootstrap_url, migrator_url, runtime_url

    assert HOST == "127.0.0.1"
    for url in (bootstrap_url(), migrator_url(), runtime_url()):
        assert "@127.0.0.1:" in url


def test_f5_launch_configuration_references_local_task_and_launcher() -> None:
    launch = json.loads((REPO_ROOT / ".vscode/launch.json").read_text(encoding="utf-8"))
    tasks = json.loads((REPO_ROOT / ".vscode/tasks.json").read_text(encoding="utf-8"))
    configs = launch["configurations"]
    local = next(c for c in configs if c["name"] == "AIEOS API — Local Development")
    assert local["program"].endswith("tools/dev/run_local_api.py")
    assert local["preLaunchTask"] == "AIEOS: Local DB Up + Migrate"
    assert local["justMyCode"] is False
    assert local["console"] == "integratedTerminal"
    task_labels = {task["label"] for task in tasks["tasks"]}
    assert "AIEOS: Local DB Up + Migrate" in task_labels
    assert "AIEOS: Local DB Status" in task_labels
    assert "AIEOS: Local DB Stop" in task_labels
    assert "AIEOS: Local DB Reset" in task_labels


def test_alembic_head_remains_tosd070001() -> None:
    from aieos.platform.runtime.readiness import EXPECTED_ALEMBIC_HEAD
    from tools.dev.constants import EXPECTED_ALEMBIC_HEAD as DEV_EXPECTED
    from tools.release.common import EXPECTED_MIGRATION_HEAD

    assert EXPECTED_ALEMBIC_HEAD == "tosd070001"
    assert DEV_EXPECTED == "tosd070001"
    assert EXPECTED_MIGRATION_HEAD == "tosd070001"


def test_openapi_digest_unchanged() -> None:
    from tools.release.common import EXPECTED_OPENAPI_SHA256, assert_openapi_digest

    assert (
        EXPECTED_OPENAPI_SHA256
        == "CCD233062672B36A4DB6C6B60E7413AF8EEC6FDAAE9550270C6879E4C4A06D7C"
    )
    assert_openapi_digest(REPO_ROOT)


def test_local_config_environ_uses_safe_local_database() -> None:
    from tools.dev.local_config import build_local_api_environ

    env = build_local_api_environ()
    assert "AIEOS_DATABASE_URL" not in env
    assert "@127.0.0.1:55432/aieos" in env["AIEOS_RUNTIME_DATABASE_URL"]
    assert env["AIEOS_RUNTIME_DATABASE_ROLE"] == "aieos_runtime"
    assert env["AIEOS_AUTH_JWKS_URI"].startswith("https://")

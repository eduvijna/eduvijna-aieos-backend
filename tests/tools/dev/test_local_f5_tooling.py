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


def test_alembic_head_remains_tosd070002() -> None:
    from aieos.platform.runtime.readiness import EXPECTED_ALEMBIC_HEAD
    from tools.dev.constants import EXPECTED_ALEMBIC_HEAD as DEV_EXPECTED
    from tools.release.common import EXPECTED_MIGRATION_HEAD

    assert EXPECTED_ALEMBIC_HEAD == "tosd080002"
    assert DEV_EXPECTED == "tosd080002"
    assert EXPECTED_MIGRATION_HEAD == "tosd080002"


def test_openapi_digest_unchanged() -> None:
    from tools.release.common import EXPECTED_OPENAPI_SHA256, assert_openapi_digest

    assert (
        EXPECTED_OPENAPI_SHA256
        == "824B389D6D4EDB2EA5D8ED3A9E5411087B566DFDCA09C2AB0CD4FDED51C4D89D"
    )
    assert_openapi_digest(REPO_ROOT)


def test_local_config_environ_uses_safe_local_database() -> None:
    from tools.dev.local_config import build_local_api_environ, resolve_local_source_git_sha

    source_sha = resolve_local_source_git_sha()
    env = build_local_api_environ(source_sha)
    assert "AIEOS_DATABASE_URL" not in env
    assert "@127.0.0.1:55432/aieos" in env["AIEOS_RUNTIME_DATABASE_URL"]
    assert env["AIEOS_RUNTIME_DATABASE_ROLE"] == "aieos_runtime"
    assert env["AIEOS_AUTH_JWKS_URI"].startswith("https://")
    assert env["AIEOS_GIT_SHA"] == source_sha


def test_build_local_api_environ_rejects_invalid_source_sha() -> None:
    from tools.dev.local_config import build_local_api_environ

    with pytest.raises(ValueError, match="40 lowercase hexadecimal"):
        build_local_api_environ("not-a-valid-git-sha")


def test_local_f5_mutation_gate_enabled() -> None:
    from aieos.platform.runtime.activation import (
        MutationActivationStatus,
        load_api_mutation_activation_gate,
    )
    from aieos.platform.runtime.models import ReleaseIdentity
    from tools.dev.local_config import (
        LOCAL_ARTIFACT_DIGEST,
        build_local_api_environ,
        resolve_local_source_git_sha,
    )

    source_sha = resolve_local_source_git_sha()
    env = build_local_api_environ(source_sha)
    assert env["AIEOS_API_MUTATION_ACTIVATION"] == "ENABLED"
    assert env["AIEOS_API_MUTATION_AUTHORIZED_GIT_SHA"] == source_sha
    assert env["AIEOS_API_MUTATION_AUTHORIZED_ARTIFACT_DIGEST"] == LOCAL_ARTIFACT_DIGEST

    release = ReleaseIdentity(
        application_version="0.1.0",
        git_sha=source_sha,
        build_id="local-f5-dev",
        artifact_digest=LOCAL_ARTIFACT_DIGEST,
    )
    decision = load_api_mutation_activation_gate(env, release).check()
    assert decision.enabled is True
    assert decision.status == MutationActivationStatus.ENABLED


def test_local_f5_teaching_work_create_mutation(postgres18) -> None:
    import os
    import uuid
    from datetime import date, timedelta

    from fastapi.testclient import TestClient

    from aieos.platform.runtime.composition import compose_api_application
    from aieos.platform.runtime.config import load_api_runtime_config
    from aieos.platform.runtime.database import create_api_runtime_engine
    from tools.dev.compose_local_api import compose_local_api_runtime_dependencies
    from tools.dev.local_config import (
        LOCAL_BEARER_TOKEN,
        LOCAL_DEV_TENANT_ID,
        build_local_api_environ,
        resolve_local_source_git_sha,
    )

    source_sha = resolve_local_source_git_sha()
    env = build_local_api_environ(source_sha)
    env["AIEOS_RUNTIME_DATABASE_URL"] = postgres18["runtime_url"]
    for key in list(os.environ):
        if key.startswith("AIEOS_"):
            del os.environ[key]
    for key, value in env.items():
        os.environ[key] = value

    config = load_api_runtime_config(env)
    engine = create_api_runtime_engine(config)
    try:
        dependencies = compose_local_api_runtime_dependencies(
            engine=engine,
            config=config,
        )
        decision = dependencies.mutation_activation_gate.check()
        assert decision.enabled is True

        app = compose_api_application(config, dependencies)
        client = TestClient(app, raise_server_exceptions=False)
        target_date = (date.today() + timedelta(days=1)).isoformat()
        response = client.post(
            "/api/v1/teaching/works",
            json={
                "intent_type": "prepare_tomorrow",
                "goal_text": "Local F5 teaching work mutation proof",
                "target_date": target_date,
                "locale": "en-IN",
                "class_label": "Grade 5B",
                "subject": "Mathematics",
                "topic": "Fractions",
            },
            headers={
                "Authorization": f"Bearer {LOCAL_BEARER_TOKEN}",
                "X-AIEOS-Tenant-ID": str(LOCAL_DEV_TENANT_ID),
                "Idempotency-Key": f"local-f5-proof-{uuid.uuid7()}",
            },
        )
        assert response.status_code == 201, response.text
        assert "mutations_not_activated" not in response.text
        body = response.json()
        assert body["goal_text"] == "Local F5 teaching work mutation proof"
    finally:
        engine.dispose()

"""Production API main entrypoint tests."""

from __future__ import annotations

import logging

import pytest
from unittest.mock import MagicMock, patch

from aieos.platform.runtime.errors import RuntimeConfigurationError

pytestmark = pytest.mark.ped_i01


def test_api_main_import_has_no_side_effects() -> None:
    import aieos.platform.runtime.entrypoints.api_main as module

    assert callable(module.main)
    assert module.__name__ == "aieos.platform.runtime.entrypoints.api_main"


def test_api_main_valid_startup_disposes_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    from aieos.platform.runtime.entrypoints import api_main

    config = object()
    engine = MagicMock()
    dependencies = object()
    app = object()
    calls: list[str] = []

    monkeypatch.setattr(api_main, "load_api_runtime_config_from_process_environment", lambda: config)
    monkeypatch.setattr(api_main, "_configure_logging", lambda _config: calls.append("log"))
    monkeypatch.setattr(api_main, "create_api_runtime_engine", lambda _config: engine)
    monkeypatch.setattr(
        api_main,
        "compose_api_runtime_dependencies",
        lambda *, engine, config: calls.append("deps") or dependencies,
    )
    monkeypatch.setattr(
        api_main,
        "compose_api_application",
        lambda _config, _deps: calls.append("app") or app,
    )
    monkeypatch.setattr(
        api_main,
        "serve_api_application",
        lambda _app: calls.append("serve"),
    )

    api_main.main([])
    assert calls == ["log", "deps", "app", "serve"]
    engine.dispose.assert_called_once()


def test_api_main_disposes_engine_when_serving_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aieos.platform.runtime.entrypoints import api_main

    config = object()
    engine = MagicMock()

    monkeypatch.setattr(api_main, "load_api_runtime_config_from_process_environment", lambda: config)
    monkeypatch.setattr(api_main, "_configure_logging", lambda _config: None)
    monkeypatch.setattr(api_main, "create_api_runtime_engine", lambda _config: engine)
    monkeypatch.setattr(
        api_main,
        "compose_api_runtime_dependencies",
        lambda *, engine, config: object(),
    )
    monkeypatch.setattr(
        api_main,
        "compose_api_application",
        lambda _config, _deps: object(),
    )
    monkeypatch.setattr(
        api_main,
        "serve_api_application",
        lambda _app: (_ for _ in ()).throw(RuntimeError("serve failed")),
    )

    with pytest.raises(SystemExit) as excinfo:
        api_main.main([])
    assert excinfo.value.code == 1
    engine.dispose.assert_called_once()


def test_api_main_missing_config_exits_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    from aieos.platform.runtime.entrypoints import api_main

    monkeypatch.setattr(
        api_main,
        "load_api_runtime_config_from_process_environment",
        lambda: (_ for _ in ()).throw(RuntimeConfigurationError("missing")),
    )

    with pytest.raises(SystemExit) as excinfo:
        api_main.main([])
    assert excinfo.value.code == 1


def test_api_main_dependency_failure_exits_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    from aieos.platform.runtime.entrypoints import api_main

    config = object()
    engine = MagicMock()
    monkeypatch.setattr(api_main, "load_api_runtime_config_from_process_environment", lambda: config)
    monkeypatch.setattr(api_main, "_configure_logging", lambda _config: None)
    monkeypatch.setattr(api_main, "create_api_runtime_engine", lambda _config: engine)
    monkeypatch.setattr(
        api_main,
        "compose_api_runtime_dependencies",
        lambda *, engine, config: (_ for _ in ()).throw(RuntimeError("dependency failed")),
    )

    with pytest.raises(SystemExit) as excinfo:
        api_main.main([])
    assert excinfo.value.code == 1
    engine.dispose.assert_called_once()


def test_api_main_configuration_error_does_not_log_secrets(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from aieos.platform.runtime.entrypoints import api_main

    secret = "SUPER_SECRET_CURSOR_VALUE"
    monkeypatch.setattr(
        api_main,
        "load_api_runtime_config_from_process_environment",
        lambda: (_ for _ in ()).throw(
            RuntimeConfigurationError(f"{secret} must not appear")
        ),
    )

    with pytest.raises(SystemExit):
        api_main.main([])
    assert secret not in caplog.text


def test_api_main_uses_serve_api_application_only(monkeypatch: pytest.MonkeyPatch) -> None:
    from aieos.platform.runtime.entrypoints import api_main

    monkeypatch.setattr(api_main, "load_api_runtime_config_from_process_environment", lambda: object())
    monkeypatch.setattr(api_main, "_configure_logging", lambda _config: None)
    monkeypatch.setattr(api_main, "create_api_runtime_engine", lambda _config: MagicMock())
    monkeypatch.setattr(
        api_main,
        "compose_api_runtime_dependencies",
        lambda *, engine, config: object(),
    )
    monkeypatch.setattr(
        api_main,
        "compose_api_application",
        lambda _config, _deps: object(),
    )
    served: list[object] = []
    monkeypatch.setattr(api_main, "serve_api_application", lambda app: served.append(app))

    api_main.main([])
    assert len(served) == 1


def test_api_main_logging_does_not_require_custom_record_fields(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from aieos.platform.runtime.entrypoints import api_main
    from aieos.platform.runtime.models import ApiRuntimeConfig, DeploymentEnvironment, ReleaseIdentity

    config = ApiRuntimeConfig(
        environment=DeploymentEnvironment.PRODUCTION,
        release_identity=ReleaseIdentity(
            application_version="0.1.0",
            git_sha="a" * 40,
            build_id="build-logging",
            artifact_digest="sha256:" + ("b" * 64),
        ),
        runtime_database_url="postgresql+psycopg://aieos_runtime:x@127.0.0.1:5432/aieos",
        runtime_database_role="aieos_runtime",
        content_schema_owner_role="aieos_content_owner",
        security_schema_owner_role="aieos_security_owner",
        migrator_role="aieos_migrator",
        cursor_signing_key=b"test-key",
        idempotency_retention=__import__("datetime").timedelta(hours=24),
        runtime_database_connect_timeout_seconds=5,
        auth_issuer="https://issuer.example.test/",
        auth_audience="aieos-api",
        auth_jwks_uri="https://issuer.example.test/.well-known/jwks.json",
    )

    caplog.set_level("INFO")
    api_main._configure_logging(config)
    logging.getLogger("uvicorn.error").info("library logger record")
    api_main._log_startup_failure("configuration", RuntimeConfigurationError("bad"))

    assert "workload=API" in caplog.text
    assert "environment=PRODUCTION" in caplog.text
    assert "library logger record" in caplog.text
    assert "startup failed category=configuration" in caplog.text
    assert "SUPER_SECRET" not in caplog.text

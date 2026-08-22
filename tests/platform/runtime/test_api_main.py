"""Production API main entrypoint tests."""

from __future__ import annotations

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

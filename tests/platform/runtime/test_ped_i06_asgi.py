"""PED-I06 ASGI server configuration contract tests."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

from tests.dbutil import REPO_ROOT

pytestmark = pytest.mark.ped_i06

ASGI_MODULE = "aieos.platform.runtime.asgi"
ASGI_PATH = REPO_ROOT / "src" / "aieos" / "platform" / "runtime" / "asgi.py"


def test_create_uvicorn_config_frozen_defaults() -> None:
    from aieos.platform.runtime.asgi import create_uvicorn_config

    class _ProbeApp:
        async def __call__(self, scope, receive, send):  # noqa: ANN001
            raise AssertionError("probe app must not be invoked by config construction")

    config = create_uvicorn_config(_ProbeApp())
    assert config.host == "0.0.0.0"
    assert config.port == 8080
    assert config.workers == 1
    assert config.loop == "asyncio"
    assert config.http == "h11"
    assert config.proxy_headers is False
    assert config.server_header is False
    assert config.reload is False
    assert config.lifespan == "on"


def test_asgi_module_import_has_no_startup_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    """Importing the ASGI module must not start a server or open network I/O."""
    started: list[str] = []

    class _BoomServer:
        def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
            started.append("Server.__init__")
            raise AssertionError("uvicorn.Server must not be constructed on import")

        def run(self, *args, **kwargs):  # noqa: ANN002, ANN003
            started.append("Server.run")
            raise AssertionError("uvicorn.Server.run must not run on import")

    # Ensure a clean import of the module under test.
    sys.modules.pop(ASGI_MODULE, None)
    import uvicorn

    monkeypatch.setattr(uvicorn, "Server", _BoomServer)
    monkeypatch.setattr(
        uvicorn,
        "run",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("uvicorn.run on import")),
    )

    mod = importlib.import_module(ASGI_MODULE)
    assert hasattr(mod, "create_uvicorn_config")
    assert hasattr(mod, "serve_api_application")
    assert started == []


def test_no_module_level_app_singleton() -> None:
    text = ASGI_PATH.read_text(encoding="utf-8")
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("app = compose_api_application") or stripped.startswith(
            "app = create_app"
        ):
            raise AssertionError(f"module-level app singleton: {line}")
    assert "compose_api_application" not in text
    assert "create_app(" not in text
    assert "tests." not in text
    assert "StubSecurityContextResolver" not in text
    assert "AllowReviewAuthorization" not in text
    assert "AllowPublicationAuthorization" not in text

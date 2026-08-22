"""Production Temporal worker main entrypoint tests."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aieos.platform.runtime.config_temporal import TemporalWorkerRuntimeConfig
from aieos.platform.runtime.errors import RuntimeConfigurationError
from aieos.platform.runtime.models import DeploymentEnvironment, ReleaseIdentity
from aieos.platform.workflows.constants import CONTENT_REVIEW_TASK_QUEUE

pytestmark = pytest.mark.gci_i07

VALID_CONFIG = TemporalWorkerRuntimeConfig(
    environment=DeploymentEnvironment.PRODUCTION,
    release_identity=ReleaseIdentity(
        application_version="0.1.0",
        git_sha="e" * 40,
        build_id="build-temporal-main",
        artifact_digest="sha256:" + ("f" * 64),
    ),
    target_host="namespace.tmprl.cloud:7233",
    namespace="aieos-production",
    api_key="secret-temporal-key",
    connect_timeout_seconds=10,
    shutdown_grace_seconds=30,
)


def test_temporal_main_import_has_no_side_effects() -> None:
    import aieos.platform.runtime.entrypoints.temporal_worker_main as module

    assert callable(module.main)
    assert callable(module.run_worker)


def test_run_worker_invokes_sdk_lifecycle() -> None:
    from aieos.platform.runtime.entrypoints import temporal_worker_main

    client = MagicMock()
    worker = MagicMock()
    worker.run = AsyncMock(return_value=None)
    worker.shutdown = AsyncMock(return_value=None)
    shutdown_event = asyncio.Event()

    async def _run(config):
        with patch.object(
            temporal_worker_main,
            "_connect_client",
            AsyncMock(return_value=client),
        ):
            with patch.object(
                temporal_worker_main,
                "create_content_review_worker",
                return_value=worker,
            ) as create_worker:
                with patch.object(
                    temporal_worker_main.asyncio,
                    "Event",
                    return_value=shutdown_event,
                ):
                    task = asyncio.create_task(temporal_worker_main.run_worker(config))
                    await asyncio.sleep(0.01)
                    shutdown_event.set()
                    await asyncio.wait_for(task, timeout=1.0)
        return create_worker

    create_worker = asyncio.run(_run(VALID_CONFIG))
    create_worker.assert_called_once_with(
        client, task_queue=CONTENT_REVIEW_TASK_QUEUE
    )
    worker.run.assert_awaited_once()
    worker.shutdown.assert_awaited_once()


def test_connect_client_uses_tls_and_api_key() -> None:
    from aieos.platform.runtime.entrypoints import temporal_worker_main

    captured: dict[str, object] = {}

    async def _fake_connect(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return MagicMock()

    with patch.object(
        temporal_worker_main.Client,
        "connect",
        new=AsyncMock(side_effect=_fake_connect),
    ):
        asyncio.run(temporal_worker_main._connect_client(VALID_CONFIG))

    assert captured["args"] == (VALID_CONFIG.target_host,)
    assert captured["kwargs"]["namespace"] == VALID_CONFIG.namespace
    assert captured["kwargs"]["api_key"] == VALID_CONFIG.api_key
    assert captured["kwargs"]["tls"] is True


def test_main_missing_config_exits_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    from aieos.platform.runtime.entrypoints import temporal_worker_main

    monkeypatch.setattr(
        temporal_worker_main,
        "load_temporal_worker_runtime_config_from_process_environment",
        lambda: (_ for _ in ()).throw(RuntimeConfigurationError("missing")),
    )

    with pytest.raises(SystemExit) as excinfo:
        temporal_worker_main.main([])
    assert excinfo.value.code == 1


def test_main_connect_failure_exits_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    from aieos.platform.runtime.entrypoints import temporal_worker_main

    monkeypatch.setattr(
        temporal_worker_main,
        "load_temporal_worker_runtime_config_from_process_environment",
        lambda: VALID_CONFIG,
    )
    monkeypatch.setattr(temporal_worker_main, "_configure_logging", lambda _config: None)

    async def _boom(_config):
        raise TimeoutError()

    monkeypatch.setattr(temporal_worker_main, "run_worker", _boom)

    with pytest.raises(SystemExit) as excinfo:
        temporal_worker_main.main([])
    assert excinfo.value.code == 1


def test_main_does_not_require_database_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aieos.platform.runtime.entrypoints import temporal_worker_main

    monkeypatch.setattr(
        temporal_worker_main,
        "load_temporal_worker_runtime_config_from_process_environment",
        lambda: VALID_CONFIG,
    )
    monkeypatch.setattr(temporal_worker_main, "_configure_logging", lambda _config: None)
    monkeypatch.setattr(
        temporal_worker_main,
        "run_worker",
        AsyncMock(return_value=None),
    )

    temporal_worker_main.main([])

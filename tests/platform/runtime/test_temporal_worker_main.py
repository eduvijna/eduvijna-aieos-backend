"""Production Temporal worker main entrypoint tests."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
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


async def _run_with_worker(
    worker: MagicMock,
    *,
    config: TemporalWorkerRuntimeConfig = VALID_CONFIG,
    shutdown_event: asyncio.Event | None = None,
) -> None:
    from aieos.platform.runtime.entrypoints import temporal_worker_main

    event = shutdown_event or asyncio.Event()
    with patch.object(
        temporal_worker_main,
        "_connect_client",
        AsyncMock(return_value=MagicMock()),
    ):
        with patch.object(
            temporal_worker_main,
            "create_content_review_worker",
            return_value=worker,
        ):
            with patch.object(temporal_worker_main.asyncio, "Event", return_value=event):
                await temporal_worker_main.run_worker(config)


def test_temporal_main_import_has_no_side_effects() -> None:
    import aieos.platform.runtime.entrypoints.temporal_worker_main as module

    assert callable(module.main)
    assert callable(module.run_worker)


def test_run_worker_raises_promptly_when_worker_run_fails_before_signal() -> None:
    worker = MagicMock()
    worker.run = AsyncMock(side_effect=RuntimeError("worker failed"))
    worker.shutdown = AsyncMock(return_value=None)

    with pytest.raises(RuntimeError, match="worker failed"):
        asyncio.run(_run_with_worker(worker))

    worker.shutdown.assert_not_awaited()


def test_run_worker_returns_promptly_when_worker_run_completes_before_signal() -> None:
    worker = MagicMock()
    worker.run = AsyncMock(return_value=None)
    worker.shutdown = AsyncMock(return_value=None)

    asyncio.run(_run_with_worker(worker))

    worker.run.assert_awaited_once()
    worker.shutdown.assert_not_awaited()


def test_run_worker_shutdown_invokes_sdk_shutdown_once() -> None:
    worker = MagicMock()
    run_started = asyncio.Event()
    release_run = asyncio.Event()

    async def _run_side_effect() -> None:
        run_started.set()
        await release_run.wait()

    worker.run = AsyncMock(side_effect=_run_side_effect)

    async def _shutdown_side_effect() -> None:
        release_run.set()

    worker.shutdown = AsyncMock(side_effect=_shutdown_side_effect)
    shutdown_event = asyncio.Event()

    async def _run() -> None:
        task = asyncio.create_task(_run_with_worker(worker, shutdown_event=shutdown_event))
        await asyncio.wait_for(run_started.wait(), timeout=1.0)
        shutdown_event.set()
        await asyncio.wait_for(task, timeout=1.0)

    asyncio.run(_run())

    worker.run.assert_awaited_once()
    worker.shutdown.assert_awaited_once()


def test_run_worker_shutdown_grace_exceeded_cancels_run_task() -> None:
    worker = MagicMock()
    hang = asyncio.Event()

    async def _hang_run() -> None:
        await hang.wait()

    worker.run = AsyncMock(side_effect=_hang_run)

    async def _slow_shutdown() -> None:
        await asyncio.sleep(10)

    worker.shutdown = AsyncMock(side_effect=_slow_shutdown)
    shutdown_event = asyncio.Event()
    config = replace(VALID_CONFIG, shutdown_grace_seconds=1)

    async def _run() -> None:
        task = asyncio.create_task(
            _run_with_worker(worker, config=config, shutdown_event=shutdown_event)
        )
        await asyncio.sleep(0.01)
        shutdown_event.set()
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(task, timeout=2.0)
        hang.set()

    asyncio.run(_run())

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


def test_temporal_main_logging_does_not_require_custom_record_fields(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from aieos.platform.runtime.entrypoints import temporal_worker_main

    caplog.set_level("INFO")
    temporal_worker_main._configure_logging(VALID_CONFIG)
    logging.getLogger("temporalio.worker").info("library logger record")
    temporal_worker_main._log_startup_failure("configuration", RuntimeConfigurationError("bad"))

    assert "workload=TEMPORAL_WORKER" in caplog.text
    assert "environment=PRODUCTION" in caplog.text
    assert "library logger record" in caplog.text
    assert "startup failed category=configuration" in caplog.text
    assert VALID_CONFIG.api_key not in caplog.text


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

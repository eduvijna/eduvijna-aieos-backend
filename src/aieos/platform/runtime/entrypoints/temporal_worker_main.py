"""Production Content Review Temporal worker executable entrypoint."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from typing import Sequence

from temporalio.client import Client

from aieos.platform.runtime.config_temporal import (
    TemporalWorkerRuntimeConfig,
    load_temporal_worker_runtime_config_from_process_environment,
)
from aieos.platform.runtime.errors import RuntimeConfigurationError
from aieos.platform.runtime.models import WorkloadKind
from aieos.platform.workflows.temporal.worker import create_content_review_worker

logger = logging.getLogger(__name__)


def _configure_logging(config: TemporalWorkerRuntimeConfig) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(levelname)s workload=%(workload)s environment=%(environment)s "
            "git_sha=%(git_sha)s %(message)s"
        ),
    )
    logging.getLogger(__name__).info(
        "temporal worker startup",
        extra={
            "workload": WorkloadKind.TEMPORAL_WORKER.value,
            "environment": config.environment.value,
            "git_sha": config.release_identity.git_sha,
        },
    )


def _log_startup_failure(category: str, exc: BaseException) -> None:
    logger.error(
        "temporal worker startup failed category=%s error_type=%s",
        category,
        type(exc).__name__,
    )


def _worker_identity(config: TemporalWorkerRuntimeConfig) -> str:
    return (
        "aieos.temporal-worker.content-review/"
        f"{config.release_identity.build_id}"
    )


async def _connect_client(config: TemporalWorkerRuntimeConfig) -> Client:
    return await asyncio.wait_for(
        Client.connect(
            config.target_host,
            namespace=config.namespace,
            api_key=config.api_key,
            tls=True,
            identity=_worker_identity(config),
        ),
        timeout=config.connect_timeout_seconds,
    )


async def run_worker(config: TemporalWorkerRuntimeConfig) -> None:
    """Connect, run the governed Content Review worker, and shut down gracefully."""
    client = await _connect_client(config)
    worker = create_content_review_worker(client, task_queue=config.task_queue)
    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _request_shutdown() -> None:
        shutdown_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _request_shutdown)
        except NotImplementedError:
            signal.signal(sig, lambda _signum, _frame: _request_shutdown())

    run_task = asyncio.create_task(worker.run())
    try:
        await shutdown_event.wait()
        logger.info("temporal worker shutdown requested")
        try:
            await asyncio.wait_for(
                worker.shutdown(),
                timeout=config.shutdown_grace_seconds,
            )
        except TimeoutError:
            logger.error("temporal worker shutdown grace exceeded category=shutdown")
            run_task.cancel()
            raise
        await run_task
    finally:
        if not run_task.done():
            run_task.cancel()
            await asyncio.gather(run_task, return_exceptions=True)


def main(argv: Sequence[str] | None = None) -> None:
    _ = argv
    try:
        config = load_temporal_worker_runtime_config_from_process_environment()
        _configure_logging(config)
        asyncio.run(run_worker(config))
    except RuntimeConfigurationError as exc:
        _log_startup_failure("configuration", exc)
        sys.exit(1)
    except TimeoutError as exc:
        _log_startup_failure("connect_or_shutdown", exc)
        sys.exit(1)
    except Exception as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        _log_startup_failure("runtime", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()

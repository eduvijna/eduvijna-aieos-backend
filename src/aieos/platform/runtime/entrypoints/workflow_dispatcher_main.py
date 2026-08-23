"""Production WORKFLOW dispatcher executable entrypoint (PED-I12).

Importing this module has no external side effects.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from typing import Sequence

from aieos.platform.runtime.config_workflow_dispatcher import (
    WorkflowDispatcherRuntimeConfig,
    load_workflow_dispatcher_runtime_config_from_process_environment,
)
from aieos.platform.runtime.errors import RuntimeConfigurationError
from aieos.platform.runtime.models import WorkloadKind
from aieos.platform.runtime.workflow_dispatcher_authority import (
    probe_workflow_dispatcher_database_authority,
)
from aieos.platform.runtime.workflow_dispatcher_database import (
    create_workflow_dispatcher_engine,
)
from aieos.platform.workflows.persistence.candidates import (
    SqlAlchemyCommandIntentCandidateRepository,
    SqlAlchemyStartIntentCandidateRepository,
)
from aieos.platform.workflows.persistence.repositories import (
    SqlAlchemyWorkflowDispatcherRepository,
)
from aieos.platform.workflows.temporal.connection import (
    connect_workflow_dispatcher_temporal,
)
from aieos.platform.workflows.temporal.daemon import (
    WorkflowDispatcherDaemon,
    build_claimed_by,
    dispatcher_config_from_runtime,
)
from aieos.platform.workflows.temporal.dispatchers import (
    ContentReviewCommandDispatcher,
    ContentReviewStartDispatcher,
)
from aieos.platform.workflows.temporal.gateway import TemporalClientReviewGateway

logger = logging.getLogger(__name__)


def _configure_logging(config: WorkflowDispatcherRuntimeConfig) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s %(message)s",
    )
    logging.getLogger(__name__).info(
        "workflow dispatcher startup workload=%s environment=%s git_sha=%s",
        WorkloadKind.WORKFLOW_DISPATCHER.value,
        config.environment.value,
        config.release_identity.git_sha,
    )


def _log_startup_failure(category: str, exc: BaseException) -> None:
    logger.error(
        "workflow dispatcher startup failed category=%s error_type=%s",
        category,
        type(exc).__name__,
    )


async def supervise_workflow_dispatcher_daemon(
    daemon: WorkflowDispatcherDaemon,
    config: WorkflowDispatcherRuntimeConfig,
    *,
    shutdown_event: asyncio.Event | None = None,
) -> None:
    """Temporal-worker-style supervision: first of daemon completion or shutdown.

    On signal/shutdown:
    - stop new passes (daemon.request_shutdown)
    - allow current pass under ``shutdown_grace_seconds``
    - cancel the daemon task if grace expires
    """
    shutdown_event = shutdown_event or asyncio.Event()
    run_task = asyncio.create_task(
        daemon.run_forever(), name="workflow-dispatcher-daemon"
    )
    shutdown_waiter = asyncio.create_task(
        shutdown_event.wait(), name="workflow-dispatcher-shutdown-waiter"
    )
    try:
        done, _pending = await asyncio.wait(
            {run_task, shutdown_waiter},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if run_task in done:
            shutdown_waiter.cancel()
            await asyncio.gather(shutdown_waiter, return_exceptions=True)
            await run_task
            return

        logger.info(
            "workflow_dispatcher shutdown_requested claimed_by=%s",
            daemon.claimed_by,
        )
        daemon.request_shutdown()
        try:
            await asyncio.wait_for(
                run_task,
                timeout=float(config.shutdown_grace_seconds),
            )
        except TimeoutError:
            logger.error(
                "workflow_dispatcher shutdown grace exceeded category=shutdown"
            )
            run_task.cancel()
            await asyncio.gather(run_task, return_exceptions=True)
            raise
    finally:
        if not shutdown_waiter.done():
            shutdown_waiter.cancel()
        await asyncio.gather(shutdown_waiter, return_exceptions=True)
        if not run_task.done():
            run_task.cancel()
            await asyncio.gather(run_task, return_exceptions=True)


async def run_workflow_dispatcher(config: WorkflowDispatcherRuntimeConfig) -> None:
    engine = create_workflow_dispatcher_engine(config)
    client = None
    try:
        probe_workflow_dispatcher_database_authority(engine, config)
        client = await connect_workflow_dispatcher_temporal(config)

        claimed_by = build_claimed_by(config)
        dispatcher_cfg = dispatcher_config_from_runtime(config, claimed_by=claimed_by)
        gateway = TemporalClientReviewGateway(client)
        tenant_repo = SqlAlchemyWorkflowDispatcherRepository(engine)
        start_dispatcher = ContentReviewStartDispatcher(
            tenant_repo, gateway, dispatcher_cfg
        )
        command_dispatcher = ContentReviewCommandDispatcher(
            tenant_repo, gateway, dispatcher_cfg
        )
        daemon = WorkflowDispatcherDaemon(
            config=config,
            start_candidate_repository=SqlAlchemyStartIntentCandidateRepository(engine),
            command_candidate_repository=SqlAlchemyCommandIntentCandidateRepository(
                engine
            ),
            start_dispatcher=start_dispatcher,
            command_dispatcher=command_dispatcher,
            claimed_by=claimed_by,
        )

        shutdown_event = asyncio.Event()
        loop = asyncio.get_running_loop()

        def _request_shutdown() -> None:
            logger.info(
                "workflow_dispatcher signal_shutdown claimed_by=%s", claimed_by
            )
            daemon.request_shutdown()
            shutdown_event.set()

        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, _request_shutdown)
            except NotImplementedError:
                signal.signal(sig, lambda _signum, _frame: _request_shutdown())

        await supervise_workflow_dispatcher_daemon(
            daemon,
            config,
            shutdown_event=shutdown_event,
        )
    finally:
        engine.dispose()
        logger.info(
            "workflow_dispatcher cleanup_complete git_sha=%s",
            config.release_identity.git_sha,
        )


def main(argv: Sequence[str] | None = None) -> None:
    _ = argv
    try:
        config = load_workflow_dispatcher_runtime_config_from_process_environment()
        _configure_logging(config)
        asyncio.run(run_workflow_dispatcher(config))
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

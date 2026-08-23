"""Production EVENT dispatcher executable entrypoint (PED-I11 / PED-I11R1).

Importing this module has no external side effects.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from typing import Sequence

from aieos.platform.events.constants import PRODUCTION_EVENT_STREAM_NAME
from aieos.platform.events.nats.connection import connect_event_dispatcher_nats
from aieos.platform.events.nats.credentials import (
    InMemoryNatsCredentials,
    NatsCredentialError,
)
from aieos.platform.events.nats.daemon import (
    EventDispatcherDaemon,
    build_claimed_by,
    outbox_config_from_runtime,
)
from aieos.platform.events.nats.dispatcher import (
    ContentOutboxDispatcher,
    OutboxDispatcherConfig,
)
from aieos.platform.events.nats.publisher import NatsJetStreamEventPublisher
from aieos.platform.events.persistence.candidates import (
    SqlAlchemyOutboxCandidateRepository,
)
from aieos.platform.events.persistence.repositories import (
    SqlAlchemyOutboxDispatcherRepository,
)
from aieos.platform.runtime.config_event_dispatcher import (
    EventDispatcherRuntimeConfig,
    load_event_dispatcher_runtime_config_from_process_environment,
)
from aieos.platform.runtime.errors import RuntimeConfigurationError
from aieos.platform.runtime.event_dispatcher_authority import (
    probe_event_dispatcher_database_authority,
)
from aieos.platform.runtime.event_dispatcher_database import (
    create_event_dispatcher_engine,
)
from aieos.platform.runtime.models import WorkloadKind

logger = logging.getLogger(__name__)


def _configure_logging(config: EventDispatcherRuntimeConfig) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s %(message)s",
    )
    logging.getLogger(__name__).info(
        "event dispatcher startup workload=%s environment=%s git_sha=%s",
        WorkloadKind.EVENT_DISPATCHER.value,
        config.environment.value,
        config.release_identity.git_sha,
    )


def _log_startup_failure(category: str, exc: BaseException) -> None:
    logger.error(
        "event dispatcher startup failed category=%s error_type=%s",
        category,
        type(exc).__name__,
    )


async def supervise_event_dispatcher_daemon(
    daemon: EventDispatcherDaemon,
    config: EventDispatcherRuntimeConfig,
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
    run_task = asyncio.create_task(daemon.run_forever(), name="event-dispatcher-daemon")
    shutdown_waiter = asyncio.create_task(
        shutdown_event.wait(), name="event-dispatcher-shutdown-waiter"
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
            "event_dispatcher shutdown_requested claimed_by=%s",
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
                "event_dispatcher shutdown grace exceeded category=shutdown"
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


async def _bounded_nats_drain(nats_client, *, grace_seconds: float) -> None:
    try:
        await asyncio.wait_for(
            nats_client.drain(),
            timeout=float(grace_seconds),
        )
    except Exception:
        try:
            await nats_client.close()
        except Exception:
            pass


async def run_event_dispatcher(config: EventDispatcherRuntimeConfig) -> None:
    engine = create_event_dispatcher_engine(config)
    credentials: InMemoryNatsCredentials | None = None
    nats_client = None
    try:
        probe_event_dispatcher_database_authority(engine, config)
        credentials = InMemoryNatsCredentials.parse(config.nats_credentials)
        nats_client = await connect_event_dispatcher_nats(config, credentials)

        claimed_by = build_claimed_by(config)
        outbox_cfg = outbox_config_from_runtime(config, claimed_by=claimed_by)
        assert isinstance(outbox_cfg, OutboxDispatcherConfig)

        dispatcher = ContentOutboxDispatcher(
            SqlAlchemyOutboxDispatcherRepository(engine),
            NatsJetStreamEventPublisher(
                nats_client,
                expected_stream=PRODUCTION_EVENT_STREAM_NAME,
            ),
            outbox_cfg,
        )
        daemon = EventDispatcherDaemon(
            config=config,
            candidate_repository=SqlAlchemyOutboxCandidateRepository(engine),
            dispatcher=dispatcher,
            claimed_by=claimed_by,
        )

        shutdown_event = asyncio.Event()
        loop = asyncio.get_running_loop()

        def _request_shutdown() -> None:
            logger.info(
                "event_dispatcher signal_shutdown claimed_by=%s", claimed_by
            )
            daemon.request_shutdown()
            shutdown_event.set()

        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, _request_shutdown)
            except NotImplementedError:
                signal.signal(sig, lambda _signum, _frame: _request_shutdown())

        await supervise_event_dispatcher_daemon(
            daemon,
            config,
            shutdown_event=shutdown_event,
        )
    finally:
        if nats_client is not None:
            await _bounded_nats_drain(
                nats_client,
                grace_seconds=float(config.shutdown_grace_seconds),
            )
        if credentials is not None:
            credentials.wipe()
        engine.dispose()
        logger.info(
            "event_dispatcher cleanup_complete git_sha=%s",
            config.release_identity.git_sha,
        )


def main(argv: Sequence[str] | None = None) -> None:
    _ = argv
    try:
        config = load_event_dispatcher_runtime_config_from_process_environment()
        _configure_logging(config)
        asyncio.run(run_event_dispatcher(config))
    except (RuntimeConfigurationError, NatsCredentialError) as exc:
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

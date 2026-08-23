"""EVENT dispatcher NATS connection composition (ADR-AIEOS-046 / PED-I11).

Importing this module creates no network connection.
"""

from __future__ import annotations

import asyncio
import logging
import ssl

from nats.aio.client import Client as NATSClient

from aieos.platform.events.nats.credentials import InMemoryNatsCredentials
from aieos.platform.runtime.config_event_dispatcher import EventDispatcherRuntimeConfig
from aieos.platform.runtime.errors import RuntimeConfigurationError

logger = logging.getLogger(__name__)


def build_verifying_ssl_context(
    *,
    ca_bundle_path: str | None = None,
) -> ssl.SSLContext:
    """TLS context with certificate verification required."""
    ctx = ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH)
    if ca_bundle_path:
        try:
            ctx.load_verify_locations(cafile=ca_bundle_path)
        except OSError as exc:
            raise RuntimeConfigurationError(
                "NATS CA bundle path is unreadable"
            ) from exc
    # Explicit fail-closed posture (never disable hostname checks or CERT_NONE).
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.check_hostname = True
    return ctx


def connection_name(config: EventDispatcherRuntimeConfig) -> str:
    return f"aieos.event-dispatcher/{config.release_identity.build_id}"


async def _close_partial_client(client: NATSClient) -> None:
    """Best-effort close of a partially constructed client (no credential logging)."""
    try:
        await client.close()
    except Exception:
        logger.info("event_dispatcher nats partial_client_close category=connect_cleanup")


async def connect_event_dispatcher_nats(
    config: EventDispatcherRuntimeConfig,
    credentials: InMemoryNatsCredentials,
) -> NATSClient:
    """Connect with in-memory JWT/NKey callbacks. No stream administration.

    ``AIEOS_EVENT_DISPATCHER_NATS_CONNECT_TIMEOUT_SECONDS`` bounds the complete
    initial establishment (outer asyncio deadline), not only the inner nats-py
    ``connect_timeout``. Post-establishment reconnect remains enabled.
    """
    ssl_ctx = build_verifying_ssl_context(ca_bundle_path=config.nats_ca_bundle_path)
    client = NATSClient()
    deadline = float(config.nats_connect_timeout_seconds)
    try:
        async with asyncio.timeout(deadline):
            await client.connect(
                servers=[config.nats_url],
                user_jwt_cb=credentials.user_jwt_cb,
                signature_cb=credentials.signature_cb,
                tls=ssl_ctx,
                connect_timeout=config.nats_connect_timeout_seconds,
                name=connection_name(config),
                allow_reconnect=True,
            )
    except TimeoutError:
        await _close_partial_client(client)
        logger.error(
            "event_dispatcher nats initial_connect_deadline category=connect_timeout"
        )
        raise TimeoutError(
            "EVENT dispatcher NATS initial connection deadline exceeded"
        ) from None
    except Exception:
        await _close_partial_client(client)
        raise
    return client

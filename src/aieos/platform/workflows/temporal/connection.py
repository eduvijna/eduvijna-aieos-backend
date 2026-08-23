"""Distinct WORKFLOW_DISPATCHER Temporal Client connection factory (PED-I12).

Does not use worker ``AIEOS_TEMPORAL_*`` credentials. Bounds the COMPLETE initial
``Client.connect`` establishment with an outer asyncio timeout.
"""

from __future__ import annotations

import asyncio
import logging

from temporalio.client import Client

from aieos.platform.runtime.config_workflow_dispatcher import (
    WorkflowDispatcherRuntimeConfig,
)

logger = logging.getLogger(__name__)


def workflow_dispatcher_client_identity(config: WorkflowDispatcherRuntimeConfig) -> str:
    return (
        "aieos.workflow-dispatcher.content-review/"
        f"{config.release_identity.build_id}"
    )


async def connect_workflow_dispatcher_temporal(
    config: WorkflowDispatcherRuntimeConfig,
) -> Client:
    """Establish a distinct WORKFLOW_DISPATCHER Temporal client (TLS + API key).

    Raises TimeoutError on outer connect deadline. Never logs the API key.
    """
    identity = workflow_dispatcher_client_identity(config)
    logger.info(
        "workflow_dispatcher temporal_connect_begin target_host=%s namespace=%s "
        "identity=%s",
        config.temporal_target_host,
        config.temporal_namespace,
        identity,
    )
    try:
        client = await asyncio.wait_for(
            Client.connect(
                config.temporal_target_host,
                namespace=config.temporal_namespace,
                api_key=config.temporal_api_key,
                tls=True,
                identity=identity,
            ),
            timeout=float(config.temporal_connect_timeout_seconds),
        )
    except TimeoutError as exc:
        raise TimeoutError(
            "WORKFLOW dispatcher Temporal initial connection deadline exceeded"
        ) from exc
    logger.info(
        "workflow_dispatcher temporal_connect_complete identity=%s",
        identity,
    )
    return client

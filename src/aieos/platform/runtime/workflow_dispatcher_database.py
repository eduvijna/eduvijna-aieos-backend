"""WORKFLOW dispatcher SQLAlchemy Engine factory (PED-I12).

Constructs the Engine only. Does not connect, change role, migrate, or set tenant.
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from aieos.platform.runtime.config_workflow_dispatcher import (
    WorkflowDispatcherRuntimeConfig,
)


def create_workflow_dispatcher_engine(config: WorkflowDispatcherRuntimeConfig) -> Engine:
    return create_engine(
        config.database_url,
        pool_pre_ping=True,
        hide_parameters=True,
        connect_args={
            "connect_timeout": config.database_connect_timeout_seconds,
        },
    )

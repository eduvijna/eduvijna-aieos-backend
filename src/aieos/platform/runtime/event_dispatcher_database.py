"""EVENT dispatcher SQLAlchemy Engine factory (PED-I11).

Constructs the Engine only. Does not connect, change role, migrate, or set tenant.
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from aieos.platform.runtime.config_event_dispatcher import EventDispatcherRuntimeConfig


def create_event_dispatcher_engine(config: EventDispatcherRuntimeConfig) -> Engine:
    return create_engine(
        config.database_url,
        pool_pre_ping=True,
        hide_parameters=True,
        connect_args={
            "connect_timeout": config.database_connect_timeout_seconds,
        },
    )

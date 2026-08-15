"""API runtime SQLAlchemy Engine construction (PED-I02).

Constructs the Engine only. Does not open a connection, run migrations,
SET ROLE, or establish tenant context.
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from aieos.platform.runtime.models import ApiRuntimeConfig


def create_api_runtime_engine(config: ApiRuntimeConfig) -> Engine:
    """Build the shared API runtime Engine from fail-closed config.

    Uses the exact ``postgresql+psycopg`` DSN contract. Connection happens later
    during readiness or Unit-of-Work use.
    """
    return create_engine(
        config.runtime_database_url,
        pool_pre_ping=True,
        hide_parameters=True,
        connect_args={
            "connect_timeout": config.runtime_database_connect_timeout_seconds,
        },
    )

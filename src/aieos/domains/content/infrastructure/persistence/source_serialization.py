"""Session-scoped migration source serialization (GCI-I13R1). SQLAlchemy only."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.domains.content.infrastructure.persistence.errors import (
    reraise_as_application_error,
)


def migration_source_lock_key(
    tenant_id: UUID,
    source_system: str,
    source_resource_type: str,
    source_resource_id: str,
) -> str:
    return f"{tenant_id}|{source_system}|{source_resource_type}|{source_resource_id}"


class SqlAlchemyMigrationSourceSerializationGate:
    """Dedicated connection + session advisory lock spanning target + FAILED txns.

    The connection is never returned to the pool while the lock is held.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @contextmanager
    def hold(
        self,
        execution_tenant_id: UUID,
        source_system: str,
        source_resource_type: str,
        source_resource_id: str,
    ) -> Iterator[None]:
        lock_key = migration_source_lock_key(
            execution_tenant_id,
            source_system,
            source_resource_type,
            source_resource_id,
        )
        connection = self._engine.connect()
        locked = False
        try:
            try:
                connection.execute(
                    text("SELECT pg_advisory_lock(hashtext(:lock_key))"),
                    {"lock_key": lock_key},
                )
                locked = True
            except Exception as exc:
                reraise_as_application_error(exc)
            yield
        finally:
            try:
                if locked:
                    try:
                        connection.execute(
                            text("SELECT pg_advisory_unlock(hashtext(:lock_key))"),
                            {"lock_key": lock_key},
                        )
                    except Exception as unlock_exc:
                        reraise_as_application_error(unlock_exc)
            finally:
                connection.close()

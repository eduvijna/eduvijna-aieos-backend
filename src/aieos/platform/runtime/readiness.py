"""Synchronous API PostgreSQL readiness probe (PED-I02).

Read-only catalog and alembic_version checks only. Excludes tenant context,
role switching, business DML, messaging brokers, and workflow runtimes.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import make_url

from aieos.platform.runtime.models import ApiRuntimeConfig

EXPECTED_ALEMBIC_HEAD = "tosd030001"
EXPECTED_POSTGRES_MAJOR = 18

_CONTENT_OWNED_SCHEMAS = (
    "content",
    "api",
    "workflow",
    "integration",
    "teaching",
    "ai",
)
_ALL_APP_SCHEMAS = (*_CONTENT_OWNED_SCHEMAS, "security")


class ReadinessCode(StrEnum):
    READY = "READY"
    DATABASE_UNAVAILABLE = "DATABASE_UNAVAILABLE"
    DATABASE_IDENTITY_MISMATCH = "DATABASE_IDENTITY_MISMATCH"
    DATABASE_ROLE_UNSAFE = "DATABASE_ROLE_UNSAFE"
    DATABASE_ROLE_MEMBERSHIP_UNSAFE = "DATABASE_ROLE_MEMBERSHIP_UNSAFE"
    DATABASE_SCHEMA_OWNER_MISMATCH = "DATABASE_SCHEMA_OWNER_MISMATCH"
    DATABASE_SCHEMA_REVISION_MISMATCH = "DATABASE_SCHEMA_REVISION_MISMATCH"
    DATABASE_SCHEMA_REVISION_UNAVAILABLE = "DATABASE_SCHEMA_REVISION_UNAVAILABLE"
    DATABASE_VERSION_MISMATCH = "DATABASE_VERSION_MISMATCH"


@dataclass(frozen=True, slots=True)
class ReadinessResult:
    ready: bool
    code: ReadinessCode


class ApiReadinessProbe(Protocol):
    def check(self) -> ReadinessResult: ...


class SqlAlchemyApiReadinessProbe:
    """Production readiness probe sharing the API runtime Engine."""

    def __init__(self, engine: Engine, config: ApiRuntimeConfig) -> None:
        self._engine = engine
        self._config = config
        self._expected_database = make_url(config.runtime_database_url).database

    def check(self) -> ReadinessResult:
        try:
            with self._engine.connect() as conn:
                identity = conn.execute(
                    text("SELECT current_user, session_user, current_database()")
                ).one()
                current_user, session_user, current_database = identity
                if (
                    current_user != self._config.runtime_database_role
                    or session_user != self._config.runtime_database_role
                    or current_database != self._expected_database
                ):
                    return ReadinessResult(
                        False, ReadinessCode.DATABASE_IDENTITY_MISMATCH
                    )

                role_attrs = conn.execute(
                    text(
                        """
                        SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole,
                               rolreplication, rolbypassrls
                        FROM pg_roles
                        WHERE rolname = current_user
                        """
                    )
                ).one()
                if role_attrs != (True, False, False, False, False, False):
                    return ReadinessResult(False, ReadinessCode.DATABASE_ROLE_UNSAFE)

                for role in (
                    self._config.content_schema_owner_role,
                    self._config.security_schema_owner_role,
                    self._config.migrator_role,
                ):
                    role_exists = conn.execute(
                        text("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname = :role)"),
                        {"role": role},
                    ).scalar_one()
                    if not role_exists:
                        continue
                    is_member = conn.execute(
                        text("SELECT pg_has_role(current_user, :role, 'MEMBER')"),
                        {"role": role},
                    ).scalar_one()
                    if is_member:
                        return ReadinessResult(
                            False, ReadinessCode.DATABASE_ROLE_MEMBERSHIP_UNSAFE
                        )

                if conn.execute(
                    text(
                        "SELECT has_database_privilege("
                        "current_user, current_database(), 'CREATE')"
                    )
                ).scalar_one():
                    return ReadinessResult(False, ReadinessCode.DATABASE_ROLE_UNSAFE)

                for schema in _ALL_APP_SCHEMAS:
                    if conn.execute(
                        text(
                            "SELECT has_schema_privilege(current_user, :schema, 'CREATE')"
                        ),
                        {"schema": schema},
                    ).scalar_one():
                        return ReadinessResult(False, ReadinessCode.DATABASE_ROLE_UNSAFE)

                owners = {
                    row[0]: row[1]
                    for row in conn.execute(
                        text(
                            """
                            SELECT n.nspname, r.rolname
                            FROM pg_namespace n
                            JOIN pg_roles r ON r.oid = n.nspowner
                            WHERE n.nspname IN
                              ('content', 'api', 'workflow', 'integration',
                               'teaching', 'ai', 'security')
                            """
                        )
                    )
                }
                for schema in _CONTENT_OWNED_SCHEMAS:
                    if owners.get(schema) != self._config.content_schema_owner_role:
                        return ReadinessResult(
                            False, ReadinessCode.DATABASE_SCHEMA_OWNER_MISMATCH
                        )
                if owners.get("security") != self._config.security_schema_owner_role:
                    return ReadinessResult(
                        False, ReadinessCode.DATABASE_SCHEMA_OWNER_MISMATCH
                    )

                runtime_owned = conn.execute(
                    text(
                        """
                        SELECT count(*) FROM pg_namespace n
                        JOIN pg_roles r ON r.oid = n.nspowner
                        WHERE n.nspname IN
                          ('content', 'api', 'workflow', 'integration',
                           'teaching', 'ai', 'security')
                          AND r.rolname = current_user
                        """
                    )
                ).scalar_one()
                if int(runtime_owned) != 0:
                    return ReadinessResult(
                        False, ReadinessCode.DATABASE_SCHEMA_OWNER_MISMATCH
                    )

                version_num = conn.execute(
                    text("SHOW server_version_num")
                ).scalar_one()
                major = int(str(version_num)) // 10000
                if major != EXPECTED_POSTGRES_MAJOR:
                    return ReadinessResult(
                        False, ReadinessCode.DATABASE_VERSION_MISMATCH
                    )

                try:
                    revisions = list(
                        conn.execute(
                            text("SELECT version_num FROM public.alembic_version")
                        ).scalars()
                    )
                except Exception:
                    return ReadinessResult(
                        False, ReadinessCode.DATABASE_SCHEMA_REVISION_UNAVAILABLE
                    )
                if len(revisions) != 1:
                    return ReadinessResult(
                        False, ReadinessCode.DATABASE_SCHEMA_REVISION_MISMATCH
                    )
                if revisions[0] != EXPECTED_ALEMBIC_HEAD:
                    return ReadinessResult(
                        False, ReadinessCode.DATABASE_SCHEMA_REVISION_MISMATCH
                    )

                return ReadinessResult(True, ReadinessCode.READY)
        except Exception:
            return ReadinessResult(False, ReadinessCode.DATABASE_UNAVAILABLE)

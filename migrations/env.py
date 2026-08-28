"""Alembic environment. Schema is created only through migrations."""

from __future__ import annotations

import os
import re
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool, text

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from aieos.domains.content.infrastructure.persistence import models as _content_models  # noqa: F401
from aieos.domains.content.infrastructure.persistence.metadata import content_metadata
from aieos.domains.teaching.infrastructure.persistence import models as _teaching_models  # noqa: F401
from aieos.platform.ai.infrastructure.persistence import (
    models as _generation_run_models,  # noqa: F401
)
from aieos.platform.api.infrastructure.persistence import models as _api_models  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = content_metadata

DATABASE_URL_ENV = "AIEOS_DATABASE_URL"
SCHEMA_OWNER_ROLE_ENV = "AIEOS_SCHEMA_OWNER_ROLE"
_ROLE_NAME = re.compile(r"^[a-z_][a-z0-9_]*$")


def _database_url() -> str:
    url = os.environ.get(DATABASE_URL_ENV)
    if not url:
        raise RuntimeError(
            f"{DATABASE_URL_ENV} must be set; Alembic does not create schema at runtime "
            "and does not embed credentials."
        )
    return url


def _schema_owner_role() -> str:
    role = os.environ.get(SCHEMA_OWNER_ROLE_ENV, "").strip()
    if not role:
        raise RuntimeError(
            f"{SCHEMA_OWNER_ROLE_ENV} must be set to the Generic Content schema-owner "
            "role; Alembic will not silently create content objects as the migrator."
        )
    if not _ROLE_NAME.fullmatch(role):
        raise RuntimeError(
            f"{SCHEMA_OWNER_ROLE_ENV} must be a lowercase unquoted PostgreSQL identifier"
        )
    return role


def _configure_kwargs() -> dict:
    return {
        "target_metadata": target_metadata,
        "include_schemas": True,
        "version_table": "alembic_version",
        "version_table_schema": "public",
    }


def run_migrations_offline() -> None:
    owner_role = _schema_owner_role()
    context.configure(
        url=_database_url(),
        literal_binds=True,
        dialect_name="postgresql",
        **_configure_kwargs(),
    )
    with context.begin_transaction():
        context.execute(f"SET LOCAL ROLE {owner_role}")
        context.run_migrations()


def run_migrations_online() -> None:
    owner_role = _schema_owner_role()
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, **_configure_kwargs())
        with context.begin_transaction():
            connection.execute(text(f"SET LOCAL ROLE {owner_role}"))
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

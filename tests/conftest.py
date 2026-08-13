"""PostgreSQL 18 fixtures for GCI-I02. Never connect to production."""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from tests.dbutil import REPO_ROOT, set_tenant

__all__ = ["REPO_ROOT", "set_tenant"]
CONTAINER_NAME = "aieos-gci-i02-pg"
POSTGRES_IMAGE = "postgres:18"
MIGRATOR_USER = "aieos_migrator"
RUNTIME_USER = "aieos_runtime"
DB_NAME = "aieos"
DB_PASSWORD = "aieos_test"
HOST_PORT = os.environ.get("AIEOS_TEST_PG_PORT", "55432")


def migrator_url(port: str) -> str:
    return (
        f"postgresql+psycopg://{MIGRATOR_USER}:{DB_PASSWORD}"
        f"@127.0.0.1:{port}/{DB_NAME}"
    )


def runtime_url(port: str) -> str:
    return (
        f"postgresql+psycopg://{RUNTIME_USER}:{DB_PASSWORD}"
        f"@127.0.0.1:{port}/{DB_NAME}"
    )


def alembic_config(url: str) -> Config:
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    os.environ["AIEOS_DATABASE_URL"] = url
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def wait_for_engine(url: str, attempts: int = 40) -> Engine:
    last_error: Exception | None = None
    for _ in range(attempts):
        engine = create_engine(url, isolation_level="AUTOCOMMIT")
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return engine
        except Exception as exc:  # noqa: BLE001 — wait loop
            last_error = exc
            engine.dispose()
            time.sleep(1)
    raise RuntimeError(f"PostgreSQL 18 did not become ready: {last_error}")


def start_postgres() -> str:
    subprocess.run(["docker", "rm", "-f", CONTAINER_NAME], check=False, capture_output=True)
    result = subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            CONTAINER_NAME,
            "-e",
            f"POSTGRES_USER={MIGRATOR_USER}",
            "-e",
            f"POSTGRES_PASSWORD={DB_PASSWORD}",
            "-e",
            f"POSTGRES_DB={DB_NAME}",
            "-p",
            f"{HOST_PORT}:5432",
            POSTGRES_IMAGE,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    if not result.stdout.strip():
        raise RuntimeError(f"docker run failed: {result.stderr}")
    return HOST_PORT


def provision_runtime_role(migrator: Engine) -> None:
    with migrator.connect() as conn:
        conn.execute(
            text(
                f"""
                DO $$
                BEGIN
                    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{RUNTIME_USER}') THEN
                        CREATE ROLE {RUNTIME_USER} LOGIN PASSWORD '{DB_PASSWORD}'
                            NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
                    END IF;
                END
                $$
                """
            )
        )
        conn.execute(text(f"GRANT CONNECT ON DATABASE {DB_NAME} TO {RUNTIME_USER}"))
        conn.execute(text(f"GRANT USAGE ON SCHEMA content TO {RUNTIME_USER}"))
        conn.execute(
            text(
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON content.contents TO {RUNTIME_USER}"
            )
        )
        conn.execute(
            text(
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON content.content_versions TO {RUNTIME_USER}"
            )
        )
        conn.execute(
            text(
                f"GRANT EXECUTE ON FUNCTION content.current_tenant_id() TO {RUNTIME_USER}"
            )
        )


@pytest.fixture(scope="session")
def postgres18() -> Iterator[dict[str, str]]:
    external = os.environ.get("AIEOS_TEST_DATABASE_URL")
    started_container = False
    if external:
        m_url = external
        r_url = os.environ.get("AIEOS_TEST_RUNTIME_DATABASE_URL", external)
        port = HOST_PORT
    else:
        port = start_postgres()
        started_container = True
        m_url = migrator_url(port)
        r_url = runtime_url(port)

    bootstrap = wait_for_engine(m_url)
    with bootstrap.connect() as conn:
        version = conn.execute(text("SHOW server_version")).scalar_one()
        if not str(version).startswith("18."):
            raise RuntimeError(f"PostgreSQL 18 required; got {version}")
    os.environ["AIEOS_DATABASE_URL"] = m_url
    cfg = alembic_config(m_url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    provision_runtime_role(bootstrap)
    try:
        yield {
            "migrator_url": m_url,
            "runtime_url": r_url,
            "server_version": str(version),
            "port": port,
        }
    finally:
        bootstrap.dispose()
        if started_container:
            subprocess.run(
                ["docker", "rm", "-f", CONTAINER_NAME],
                check=False,
                capture_output=True,
            )


@pytest.fixture(scope="session")
def migrator_engine(postgres18: dict[str, str]) -> Iterator[Engine]:
    engine = create_engine(postgres18["migrator_url"])
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def runtime_engine(postgres18: dict[str, str]) -> Iterator[Engine]:
    engine = create_engine(postgres18["runtime_url"])
    try:
        yield engine
    finally:
        engine.dispose()

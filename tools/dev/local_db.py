"""Local PostgreSQL 18 container lifecycle for Cursor F5 development.

LOCAL DEVELOPMENT ONLY — NEVER PRODUCTION.

Usage:
  uv run python tools/dev/local_db.py up
  uv run python tools/dev/local_db.py status
  uv run python tools/dev/local_db.py stop
  uv run python tools/dev/local_db.py reset --confirm
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Sequence

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from sqlalchemy import create_engine, text

from tools.dev.constants import (
    BOOTSTRAP_USER,
    CONTAINER_NAME,
    DB_NAME,
    DB_PASSWORD,
    EXPECTED_ALEMBIC_HEAD,
    HOST,
    HOST_PORT,
    POSTGRES_IMAGE,
    VOLUME_NAME,
)
from tools.dev.postgres_substrate import (
    assert_postgres18,
    bootstrap_url,
    provision_and_migrate,
    read_alembic_head,
    verify_alembic_head,
    wait_for_engine,
)


def _run(cmd: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=check, capture_output=True, text=True)


def ensure_docker_available() -> None:
    try:
        result = _run(["docker", "info"])
    except FileNotFoundError as exc:
        raise RuntimeError(
            "Docker is not available. Start Docker Desktop and retry."
        ) from exc
    if result.returncode != 0:
        raise RuntimeError(
            "Docker daemon is not reachable. Start Docker Desktop and retry."
        )


def _container_state() -> str | None:
    result = _run(
        [
            "docker",
            "ps",
            "-a",
            "--filter",
            f"name=^{CONTAINER_NAME}$",
            "--format",
            "{{.State}}",
        ],
        check=False,
    )
    state = result.stdout.strip()
    return state or None


def _container_running() -> bool:
    return _container_state() == "running"


def start_container() -> None:
    state = _container_state()
    if state == "running":
        return
    if state is not None:
        _run(["docker", "start", CONTAINER_NAME])
        return
    _run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            CONTAINER_NAME,
            "-e",
            f"POSTGRES_USER={BOOTSTRAP_USER}",
            "-e",
            f"POSTGRES_PASSWORD={DB_PASSWORD}",
            "-e",
            f"POSTGRES_DB={DB_NAME}",
            "-p",
            f"{HOST}:{HOST_PORT}:5432",
            "-v",
            f"{VOLUME_NAME}:/var/lib/postgresql",
            POSTGRES_IMAGE,
        ]
    )


def cmd_up() -> int:
    ensure_docker_available()
    start_container()
    bootstrap = provision_and_migrate()
    version = assert_postgres18(bootstrap)
    head = verify_alembic_head(bootstrap)
    bootstrap.dispose()
    print(
        f"AIEOS local PostgreSQL ready: {CONTAINER_NAME} "
        f"PostgreSQL {version} alembic={head} "
        f"host={HOST} port={HOST_PORT} db={DB_NAME}"
    )
    return 0


def cmd_status() -> int:
    ensure_docker_available()
    state = _container_state()
    if state is None:
        print(f"container={CONTAINER_NAME} state=absent")
        return 0
    print(f"container={CONTAINER_NAME} state={state}")
    if not _container_running():
        return 0
    bootstrap = wait_for_engine(bootstrap_url())
    try:
        version = assert_postgres18(bootstrap)
        head = read_alembic_head(bootstrap)
        print(
            f"postgresql={version} alembic={head!r} "
            f"expected={EXPECTED_ALEMBIC_HEAD!r} "
            f"host={HOST} port={HOST_PORT} db={DB_NAME}"
        )
    finally:
        bootstrap.dispose()
    return 0


def cmd_stop() -> int:
    ensure_docker_available()
    state = _container_state()
    if state is None:
        print(f"container={CONTAINER_NAME} already absent")
        return 0
    _run(["docker", "stop", CONTAINER_NAME], check=False)
    print(f"stopped {CONTAINER_NAME} (volume {VOLUME_NAME} retained)")
    return 0


def cmd_reset(*, confirm: bool) -> int:
    if not confirm:
        print(
            "Refusing destructive reset without --confirm. "
            "This removes the local database volume and all local data.",
            file=sys.stderr,
        )
        return 1
    ensure_docker_available()
    _run(["docker", "rm", "-f", CONTAINER_NAME], check=False)
    _run(["docker", "volume", "rm", "-f", VOLUME_NAME], check=False)
    print(f"reset complete: removed {CONTAINER_NAME} and volume {VOLUME_NAME}")
    return 0


def table_counts() -> dict[str, int]:
    """Return row counts for documented inspection tables."""
    tables = (
        "teaching.works",
        "teaching.assignments",
        "teaching.executions",
        "teaching.execution_content_bindings",
        "teaching.execution_observations",
        "teaching.work_remediation_origins",
        "content.contents",
        "content.content_versions",
    )
    engine = create_engine(bootstrap_url())
    counts: dict[str, int] = {}
    try:
        with engine.connect() as conn:
            for qualified in tables:
                schema, table = qualified.split(".", 1)
                exists = conn.execute(
                    text(
                        """
                        SELECT EXISTS (
                            SELECT 1 FROM information_schema.tables
                            WHERE table_schema = :schema AND table_name = :table
                        )
                        """
                    ),
                    {"schema": schema, "table": table},
                ).scalar_one()
                if not exists:
                    counts[qualified] = -1
                    continue
                counts[qualified] = conn.execute(
                    text(f"SELECT COUNT(*) FROM {qualified}")
                ).scalar_one()
    finally:
        engine.dispose()
    return counts


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AIEOS local PostgreSQL developer tooling")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("up", help="Start/reuse container, provision roles, migrate, verify head")
    sub.add_parser("status", help="Show container and database status")
    sub.add_parser("stop", help="Stop container without destroying data volume")
    reset_parser = sub.add_parser("reset", help="Destroy container and named volume")
    reset_parser.add_argument(
        "--confirm",
        action="store_true",
        help="Required explicit confirmation for destructive reset",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.command == "up":
        return cmd_up()
    if args.command == "status":
        return cmd_status()
    if args.command == "stop":
        return cmd_stop()
    if args.command == "reset":
        return cmd_reset(confirm=args.confirm)
    raise AssertionError(f"unknown command: {args.command}")


if __name__ == "__main__":
    sys.exit(main())

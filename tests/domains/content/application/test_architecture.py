"""GCI-I03 application-layer architecture boundaries."""

from __future__ import annotations

import ast
from pathlib import Path

from tests.dbutil import REPO_ROOT

APPLICATION_ROOT = (
    REPO_ROOT / "src" / "aieos" / "domains" / "content" / "application"
)
DOMAIN_ROOT = REPO_ROOT / "src" / "aieos" / "domains" / "content" / "domain"
FORBIDDEN = (
    "sqlalchemy",
    "alembic",
    "psycopg",
    "psycopg2",
    "asyncpg",
    "fastapi",
    "starlette",
    "temporalio",
    "nats",
    "openai",
    "anthropic",
)


def _import_violations(root: Path) -> list[str]:
    violations: list[str] = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in FORBIDDEN:
                        violations.append(f"{path.name}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module.split(".")[0] in FORBIDDEN:
                    violations.append(f"{path.name}: from {node.module}")
    return violations


def test_application_layer_has_no_persistence_or_platform_imports() -> None:
    assert APPLICATION_ROOT.is_dir()
    assert _import_violations(APPLICATION_ROOT) == []


def test_domain_layer_remains_persistence_free() -> None:
    assert _import_violations(DOMAIN_ROOT) == []

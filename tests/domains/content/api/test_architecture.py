"""GCI-I04 architecture boundaries for Content HTTP adapters."""

from __future__ import annotations

import ast
from pathlib import Path

from tests.dbutil import REPO_ROOT

API_ROOT = REPO_ROOT / "src" / "aieos" / "domains" / "content" / "api"
APPLICATION_ROOT = REPO_ROOT / "src" / "aieos" / "domains" / "content" / "application"
DOMAIN_ROOT = REPO_ROOT / "src" / "aieos" / "domains" / "content" / "domain"
SRC_ROOT = REPO_ROOT / "src" / "aieos"

API_FORBIDDEN = ("sqlalchemy", "alembic", "psycopg", "psycopg2", "asyncpg")
APP_DOMAIN_FORBIDDEN = API_FORBIDDEN + ("fastapi", "starlette", "pydantic", "nats", "temporalio")


def _import_violations(root: Path, forbidden: tuple[str, ...]) -> list[str]:
    violations: list[str] = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in forbidden:
                        violations.append(f"{path.name}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module.split(".")[0] in forbidden:
                    violations.append(f"{path.name}: from {node.module}")
    return violations


def test_api_layer_does_not_import_persistence_drivers() -> None:
    assert API_ROOT.is_dir()
    assert _import_violations(API_ROOT, API_FORBIDDEN) == []


def test_api_routes_do_not_import_persistence_models_or_tables() -> None:
    hits: list[str] = []
    for path in API_ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for needle in (
            "contents_table",
            "content_versions_table",
            "SqlAlchemy",
            "create_engine",
            "psycopg",
        ):
            if needle in text:
                hits.append(f"{path.name}:{needle}")
    assert hits == []


def test_application_and_domain_remain_http_and_persistence_free() -> None:
    assert _import_violations(APPLICATION_ROOT, APP_DOMAIN_FORBIDDEN) == []
    assert _import_violations(DOMAIN_ROOT, APP_DOMAIN_FORBIDDEN) == []


def test_get_does_not_perform_privileged_second_lookup() -> None:
    text = (APPLICATION_ROOT / "queries.py").read_text(encoding="utf-8")
    assert "bootstrap" not in text
    assert "BYPASSRLS" not in text
    assert text.count(".get(") == 1


def test_no_gci_i08_or_later_http_or_intent_structures() -> None:
    routes = (API_ROOT / "v1" / "routes.py").read_text(encoding="utf-8")
    for needle in ("PATCH", "/publish", "/archive", "review-queue", "/reviews"):
        assert needle not in routes
    hits: list[str] = []
    for path in (REPO_ROOT / "migrations").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for needle in (
            "outbox_messages",
            "audit_events",
            "publications",
            "review_queue",
        ):
            if needle in text:
                hits.append(f"{path.name}:{needle}")
    assert hits == []


def test_review_repository_is_insert_read_only() -> None:
    source = (
        REPO_ROOT
        / "src"
        / "aieos"
        / "domains"
        / "content"
        / "infrastructure"
        / "persistence"
        / "repositories.py"
    ).read_text(encoding="utf-8")
    marker = "class SqlAlchemyReviewDecisionRepository:"
    start = source.index(marker)
    body = source[start:]
    assert "def insert(" in body
    assert "def get(" in body
    assert "def get_for_version(" in body
    assert "def update(" not in body
    assert "def delete(" not in body
    assert ".commit(" not in body
    assert ".rollback(" not in body


def test_no_nats_outbox_or_publication_and_migration_chain() -> None:
    hits: list[str] = []
    domains = SRC_ROOT / "domains"
    for path in domains.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for needle in ("temporalio", "nats.", "import nats", "outbox_messages", "audit_events"):
            if needle in text:
                hits.append(f"{path.relative_to(SRC_ROOT)}:{needle}")
    for path in SRC_ROOT.rglob("*.py"):
        if "domains" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for needle in ("nats.", "import nats", "outbox_messages", "audit_events"):
            if needle in text:
                hits.append(f"{path.relative_to(SRC_ROOT)}:{needle}")
    assert hits == []
    versions = sorted(
        path.name
        for path in (REPO_ROOT / "migrations" / "versions").glob("*.py")
        if path.name != "__init__.py"
    )
    assert versions == [
        "gcii020001_content_schema.py",
        "gcii050001_api_idempotency.py",
        "gcii060001_review_decisions.py",
        "gcii070001_workflow_intents.py",
    ]

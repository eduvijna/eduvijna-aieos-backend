"""GCI-I08 architecture boundaries for outbox events and NATS."""

from __future__ import annotations

import ast
from pathlib import Path

from aieos.platform.events.constants import (
    EMITTED_CONTENT_EVENT_TYPES,
    EVENT_CONTENT_ARCHIVED_V1,
    EVENT_CONTENT_PUBLISHED_V1,
)
from tests.dbutil import REPO_ROOT

API_ROOT = REPO_ROOT / "src" / "aieos" / "domains" / "content" / "api"
APPLICATION_ROOT = REPO_ROOT / "src" / "aieos" / "domains" / "content" / "application"
DOMAIN_ROOT = REPO_ROOT / "src" / "aieos" / "domains" / "content" / "domain"
DOMAINS_ROOT = REPO_ROOT / "src" / "aieos" / "domains"
WORKFLOW_TEMPORAL = REPO_ROOT / "src" / "aieos" / "platform" / "workflows" / "temporal"
FRONTEND_HINT = REPO_ROOT.parent / "eduvijna-aieos-frontend"


def _import_roots(root: Path, forbidden: tuple[str, ...]) -> list[str]:
    violations: list[str] = []
    if not root.exists():
        return violations
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in forbidden:
                        violations.append(f"{path}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module.split(".")[0] in forbidden:
                    violations.append(f"{path}: from {node.module}")
    return violations


def _publish_call_violations(root: Path) -> list[str]:
    violations: list[str] = []
    for path in (root / "application", root / "domain"):
        if not path.exists():
            continue
        for file_path in path.rglob("*.py"):
            # PublishContentService.publish is the Content mutation command, not NATS.
            if file_path.name == "publish.py":
                continue
            tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    if node.func.attr == "publish":
                        violations.append(f"{file_path}: .publish() call")
    return violations


def test_no_nats_under_domains() -> None:
    assert _import_roots(DOMAINS_ROOT, ("nats",)) == []


def test_fastapi_routes_do_not_import_nats() -> None:
    assert _import_roots(API_ROOT, ("nats",)) == []


def test_content_review_workflow_has_no_nats() -> None:
    content_review = (WORKFLOW_TEMPORAL / "content_review.py").read_text(encoding="utf-8")
    assert "nats" not in content_review
    assert _import_roots(WORKFLOW_TEMPORAL, ("nats",)) == []


def test_frontend_has_no_nats_py_when_present() -> None:
    if not FRONTEND_HINT.exists():
        return
    hits: list[str] = []
    for path in FRONTEND_HINT.rglob("*"):
        if path.suffix.lower() not in {".ts", ".tsx", ".js", ".jsx", ".py", ".md"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if "nats-py" in text or "import nats" in text or "from nats" in text:
            hits.append(str(path))
    assert hits == []


def test_domain_and_application_never_call_nats_publish() -> None:
    content_root = REPO_ROOT / "src" / "aieos" / "domains" / "content"
    assert _publish_call_violations(content_root) == []


def test_emitted_content_events_include_published_not_archived() -> None:
    assert EVENT_CONTENT_PUBLISHED_V1 in EMITTED_CONTENT_EVENT_TYPES
    assert EVENT_CONTENT_ARCHIVED_V1 not in EMITTED_CONTENT_EVENT_TYPES


def test_migration_chain_and_forbidden_tables() -> None:
    hits: list[str] = []
    for path in (REPO_ROOT / "migrations").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for needle in ("consumer_inbox", "audit_events"):
            if needle in text:
                hits.append(f"{path.name}:{needle}")
    assert hits == []
    versions = sorted(
        path.name
        for path in (REPO_ROOT / "migrations" / "versions").glob("*.py")
        if path.name != "__init__.py"
    )
    assert versions == [
        "adra045001_dispatcher_candidate_authority.py",
        "gcii020001_content_schema.py",
        "gcii050001_api_idempotency.py",
        "gcii060001_review_decisions.py",
        "gcii070001_workflow_intents.py",
        "gcii080001_outbox_messages.py",
        "gcii090001_publications.py",
        "gcii100001_version_asset_refs.py",
        "gcii110001_ai_provenance.py",
        "gcii130001_migration_import.py",
        "pedi090001_security_authority.py",
        "pedi10b2001_asset_authority_sor.py",
        "pedi10b6001_asset_security_audit.py",
        "saii020001_security_audit_ledger.py",
    ]


def test_no_gci_i13_archive_routes() -> None:
    routes = (API_ROOT / "v1" / "routes.py").read_text(encoding="utf-8")
    assert "/actions/publish" in routes
    assert "/teacher-os/review-queue" in routes
    for needle in ("/archive", "/reviews", "version_asset_refs", "/generate"):
        assert needle not in routes


def test_offline_sql_assumes_owner_before_outbox_ddl() -> None:
    import io
    from contextlib import redirect_stdout

    from alembic import command

    from tests.conftest import SCHEMA_OWNER_ROLE, alembic_config

    cfg = alembic_config("postgresql+psycopg://offline-check/unused")
    output = io.StringIO()
    with redirect_stdout(output):
        command.upgrade(cfg, "base:head", sql=True)
    sql_text = output.getvalue()
    role_stmt = f"SET LOCAL ROLE {SCHEMA_OWNER_ROLE}"
    create_table = "CREATE TABLE integration.outbox_messages"
    role_at = sql_text.find(role_stmt)
    table_at = sql_text.find(create_table)
    begin_at = sql_text.upper().find("BEGIN")
    assert begin_at != -1 and role_at != -1 and table_at != -1
    assert begin_at < role_at < table_at

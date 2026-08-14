"""GCI-I07 architecture boundaries for Temporal and workflow intents."""

from __future__ import annotations

import ast
from pathlib import Path

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


def test_no_temporalio_under_domains() -> None:
    assert _import_roots(DOMAINS_ROOT, ("temporalio",)) == []


def test_application_and_domain_remain_temporal_free() -> None:
    assert _import_roots(APPLICATION_ROOT, ("temporalio",)) == []
    assert _import_roots(DOMAIN_ROOT, ("temporalio",)) == []


def test_fastapi_routes_do_not_import_temporal() -> None:
    assert _import_roots(API_ROOT, ("temporalio",)) == []
    routes = (API_ROOT / "v1" / "routes.py").read_text(encoding="utf-8")
    assert "Temporal" not in routes
    assert "workflow_instance_id" not in routes


def test_workflow_definition_has_no_sqlalchemy_nats_or_ai() -> None:
    content_review = (WORKFLOW_TEMPORAL / "content_review.py").read_text(encoding="utf-8")
    for needle in ("sqlalchemy", "psycopg", "nats", "openai", "anthropic"):
        assert needle not in content_review
    assert _import_roots(WORKFLOW_TEMPORAL, ("nats", "openai", "anthropic")) == []


def test_content_http_path_does_not_call_temporal() -> None:
    review = (APPLICATION_ROOT / "review.py").read_text(encoding="utf-8")
    assert "temporalio" not in review
    assert "TemporalClient" not in review
    assert "start_workflow" not in review


def test_no_audit_or_inbox_tables_and_migration_chain() -> None:
    hits: list[str] = []
    for path in (REPO_ROOT / "src").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for needle in ("audit_events", "consumer_inbox"):
            if needle in text:
                hits.append(f"{path.relative_to(REPO_ROOT)}:{needle}")
    assert hits == []
    assert _import_roots(DOMAINS_ROOT, ("nats",)) == []
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
        "gcii080001_outbox_messages.py",
        "gcii090001_publications.py",
        "gcii100001_version_asset_refs.py",
        "gcii110001_ai_provenance.py",
        "gcii130001_migration_import.py",
    ]


def test_frontend_has_no_temporalio_when_present() -> None:
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
        if "temporalio" in text or "from temporalio" in text:
            hits.append(str(path))
    assert hits == []

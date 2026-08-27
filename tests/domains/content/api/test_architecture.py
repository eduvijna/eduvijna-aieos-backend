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

_EXPECTED_MIGRATIONS = [
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
    "tosd020001_teaching_work.py",
    "tosd030001_generation_runs.py",
]


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


def test_no_gci_i14_or_unauthorized_structures() -> None:
    """Adversarial coverage lives under tests/domains/content/adversarial/; no gcii140001 DDL."""
    routes = (API_ROOT / "v1" / "routes.py").read_text(encoding="utf-8")
    for needle in (
        "PATCH",
        "/archive",
        "/reviews",
        "version_asset_refs",
        "/generate",
        "/ai",
        "/migrate",
        "/imports",
        "/legacy",
    ):
        assert needle not in routes
    assert "/actions/publish" in routes
    assert "/teacher-os/review-queue" in routes
    assert '"/teacher-os/review-queue/{content_id}/versions/{version_id}"' in routes
    assert '"/teacher-os/review-queue/{content_id}"' not in routes
    hits: list[str] = []
    allowed_generation_runs = {"tosd030001_generation_runs.py", "env.py"}
    for path in (REPO_ROOT / "migrations").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for needle in (
            "audit_events",
            "review_queue",
            "consumer_inbox",
            "asset_archive",
            "prompt_executions",
            "ai_models",
            "ai_providers",
            "gcii120001",
            "gcii140001",
        ):
            if needle in text:
                hits.append(f"{path.name}:{needle}")
        if "generation_runs" in text and path.name not in allowed_generation_runs:
            hits.append(f"{path.name}:generation_runs")
    assert hits == []
    assert (
        REPO_ROOT / "migrations" / "versions" / "gcii100001_version_asset_refs.py"
    ).is_file()
    assert (
        REPO_ROOT / "migrations" / "versions" / "gcii110001_ai_provenance.py"
    ).is_file()
    assert (
        REPO_ROOT / "migrations" / "versions" / "gcii130001_migration_import.py"
    ).is_file()
    assert not (
        REPO_ROOT / "migrations" / "versions" / "gcii120001_review_queue.py"
    ).exists()
    assert not (
        REPO_ROOT / "migrations" / "versions" / "gcii140001_adversarial.py"
    ).exists()
    assert (REPO_ROOT / "tests" / "domains" / "content" / "adversarial").is_dir()


def test_no_legacy_sql_or_postgrest_in_application_domain() -> None:
    hits: list[str] = []
    for root in (APPLICATION_ROOT, DOMAIN_ROOT):
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            lowered = text.lower()
            if "edu.content" in text or "postgrest" in lowered:
                hits.append(str(path.relative_to(REPO_ROOT)))
            if "from eduvijna" in text or "import eduvijna" in text:
                hits.append(str(path.relative_to(REPO_ROOT)))
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
    end = source.index("class SqlAlchemyPublicationRepository:")
    body = source[start:end]
    assert "def insert(" in body
    assert "def get(" in body
    assert "def get_for_version(" in body
    assert "def update(" not in body
    assert "def delete(" not in body
    assert ".commit(" not in body
    assert ".rollback(" not in body


def test_publication_repository_is_insert_read_only() -> None:
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
    marker = "class SqlAlchemyPublicationRepository:"
    start = source.index(marker)
    end = source.index("class SqlAlchemyVersionAssetRefRepository:")
    body = source[start:end]
    assert "def insert(" in body
    assert "def get(" in body
    assert "def get_for_version(" in body
    assert "def update(" not in body
    assert "def delete(" not in body
    assert ".commit(" not in body
    assert ".rollback(" not in body
    routes = (API_ROOT / "v1" / "routes.py").read_text(encoding="utf-8")
    assert "/actions/publish" in routes
    for needle in ("version_asset_refs", "/archive", "PUBLISHED", "audit_events"):
        assert needle not in routes
    for path in (REPO_ROOT / "migrations").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for needle in ("audit_events", "consumer_inbox"):
            assert needle not in text, f"{path.name}:{needle}"


def test_version_asset_ref_repository_is_insert_read_only() -> None:
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
    marker = "class SqlAlchemyVersionAssetRefRepository:"
    start = source.index(marker)
    end = source.index("def _queue_item_from_row")
    body = source[start:end]
    assert "def insert_many(" in body
    assert "def list_for_version(" in body
    assert "def update(" not in body
    assert "def delete(" not in body
    assert ".commit(" not in body
    assert ".rollback(" not in body


def test_review_queue_repository_is_read_only() -> None:
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
    marker = "class SqlAlchemyReviewQueueReadRepository:"
    start = source.index(marker)
    body = source[start:]
    assert "def list_page(" in body
    assert "def get_item(" in body
    assert "def insert(" not in body
    assert "def insert_many(" not in body
    assert "def update(" not in body
    assert "def delete(" not in body
    assert ".commit(" not in body
    assert ".rollback(" not in body
    assert "enqueue" not in body
    assert "dequeue" not in body


def test_no_nats_under_domains_routes_and_migration_chain() -> None:
    hits: list[str] = []
    domains = SRC_ROOT / "domains"
    for path in domains.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for needle in ("temporalio", "nats.", "import nats", "audit_events"):
            if needle in text:
                hits.append(f"{path.relative_to(SRC_ROOT)}:{needle}")
    for path in (API_ROOT).rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for needle in ("nats.", "import nats"):
            if needle in text:
                hits.append(f"{path.relative_to(SRC_ROOT)}:{needle}")
    for path in SRC_ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "audit_events" in text or "consumer_inbox" in text:
            hits.append(f"{path.relative_to(SRC_ROOT)}:forbidden-audit-or-inbox")
    assert hits == []
    versions = sorted(
        path.name
        for path in (REPO_ROOT / "migrations" / "versions").glob("*.py")
        if path.name != "__init__.py"
    )
    assert versions == _EXPECTED_MIGRATIONS

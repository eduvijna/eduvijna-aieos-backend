"""SAI-I01 architecture boundary tests. No persistence or Content wiring."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.dbutil import REPO_ROOT

pytestmark = pytest.mark.sai_i01

AUDIT_ROOT = REPO_ROOT / "src" / "aieos" / "platform" / "security" / "audit"
CONTENT_ROOT = REPO_ROOT / "src" / "aieos" / "domains" / "content"
MIGRATIONS = REPO_ROOT / "migrations" / "versions"

FORBIDDEN = (
    "fastapi",
    "starlette",
    "pydantic",
    "sqlalchemy",
    "alembic",
    "psycopg",
    "psycopg2",
    "asyncpg",
    "nats",
    "temporalio",
    "openai",
    "anthropic",
    "google",
    "boto3",
    "botocore",
    "postgrest",
    "eduvijna",
)

_EXPECTED_MIGRATIONS = [
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


def _import_violations(root: Path, forbidden: tuple[str, ...]) -> list[str]:
    hits: list[str] = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in forbidden:
                        hits.append(f"{path.name}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module.split(".")[0] in forbidden:
                    hits.append(f"{path.name}: from {node.module}")
    return hits


def _source_mentions(root: Path, needles: tuple[str, ...]) -> list[str]:
    hits: list[str] = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for needle in needles:
            if needle in text:
                hits.append(f"{path.relative_to(REPO_ROOT)}:{needle}")
    return hits


def test_audit_package_framework_neutral() -> None:
    assert AUDIT_ROOT.is_dir()
    assert _import_violations(AUDIT_ROOT, FORBIDDEN) == []


def test_audit_package_has_no_sql() -> None:
    hits: list[str] = []
    for path in AUDIT_ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8").lower()
        for needle in ("select ", "insert into", "create table", "sqlalchemy", "alembic"):
            if needle in text:
                hits.append(f"{path.name}:{needle}")
    assert hits == []


def test_content_domain_does_not_import_audit_yet() -> None:
    hits = _source_mentions(
        CONTENT_ROOT,
        (
            "platform.security.audit",
            "SecurityMutationAuditRepository",
            "build_security_mutation_audit_record",
            "SecurityMutationAuditRecord",
        ),
    )
    assert hits == []


def test_no_audit_db_migration_or_saii_revision() -> None:
    versions = sorted(p.name for p in MIGRATIONS.glob("*.py") if p.name != "__init__.py")
    assert versions == _EXPECTED_MIGRATIONS
    for path in MIGRATIONS.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for needle in (
            "security.audit_records",
            "saii010001",
            "gcii150001",
            "gcii140001",
            "CREATE SCHEMA security",
        ):
            assert needle not in text, f"{path.name}:{needle}"


def test_mutation_event_context_and_trusted_security_context_unchanged() -> None:
    from aieos.platform.events.models import MutationEventContext
    from aieos.platform.security.context import TrustedSecurityContext
    from dataclasses import fields

    assert {f.name for f in fields(MutationEventContext)} == {
        "correlation_id",
        "causation_id",
        "actor_principal_id",
        "effective_actor_id",
    }
    assert {f.name for f in fields(TrustedSecurityContext)} == {
        "tenant_id",
        "principal_id",
    }

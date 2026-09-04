"""SAI-I01 contract architecture + advanced SAI-I02 ledger boundary assertions."""

from __future__ import annotations

import ast
from dataclasses import fields
from pathlib import Path

import pytest

from tests.dbutil import REPO_ROOT

pytestmark = pytest.mark.sai_i01

AUDIT_ROOT = REPO_ROOT / "src" / "aieos" / "platform" / "security" / "audit"
AUDIT_CONTRACT_FILES = (
    AUDIT_ROOT / "__init__.py",
    AUDIT_ROOT / "actions.py",
    AUDIT_ROOT / "builders.py",
    AUDIT_ROOT / "errors.py",
    AUDIT_ROOT / "identities.py",
    AUDIT_ROOT / "models.py",
    AUDIT_ROOT / "ports.py",
)
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
    "tosd030002_generation_run_work_fence.py",
    "tosd040001_multi_artifact_provenance_and_generation_fences.py",
    "tosd060001_teaching_assignments.py",
    "tosd060002_teaching_assignment_audit.py",
    "tosd070001_teaching_executions.py",
    "tosd070002_teaching_execution_audit.py",
    "tosd080001_classroom_assessments.py",
    "tosd080002_classroom_assessment_audit.py",
    "tosd090001_remediation_work_origin.py",
]


def _import_violations(paths: tuple[Path, ...], forbidden: tuple[str, ...]) -> list[str]:
    hits: list[str] = []
    for path in paths:
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


def test_audit_contract_package_framework_neutral() -> None:
    assert all(path.is_file() for path in AUDIT_CONTRACT_FILES)
    assert _import_violations(AUDIT_CONTRACT_FILES, FORBIDDEN) == []


def test_audit_contract_package_has_no_sql() -> None:
    hits: list[str] = []
    for path in AUDIT_CONTRACT_FILES:
        text = path.read_text(encoding="utf-8").lower()
        for needle in ("select ", "insert into", "create table", "sqlalchemy", "alembic"):
            if needle in text:
                hits.append(f"{path.name}:{needle}")
    assert hits == []


def test_content_domain_does_not_import_audit_yet() -> None:
    """SAI-I01 boundary: Content must not import audit until SAI-I03.

    Kept as a historical assertion renamed via later SAI-I03 architecture suite.
    """
    # Deferred: SAI-I03 wires Content application audit; see test_sai_i03_architecture.
    pytest.skip("superseded by SAI-I03 Content audit wiring")


def test_sai_i02_ledger_exists_without_saii010001_or_content_wiring() -> None:
    versions = sorted(p.name for p in MIGRATIONS.glob("*.py") if p.name != "__init__.py")
    assert versions == _EXPECTED_MIGRATIONS
    ledger = MIGRATIONS / "saii020001_security_audit_ledger.py"
    text = ledger.read_text(encoding="utf-8")
    assert "security.audit_records" in text
    assert "CREATE SCHEMA security" in text
    assert "saii010001" not in text
    for path in MIGRATIONS.rglob("*.py"):
        body = path.read_text(encoding="utf-8")
        assert "saii010001" not in body
        assert "gcii140001" not in body
        assert "gcii150001" not in body
        assert "saii030001" not in body


def test_mutation_event_context_and_trusted_security_context_unchanged() -> None:
    from aieos.platform.events.models import MutationEventContext
    from aieos.platform.security.context import TrustedSecurityContext

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

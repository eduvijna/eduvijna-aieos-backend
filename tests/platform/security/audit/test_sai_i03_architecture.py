"""SAI-I03 architecture boundaries: API audit wired; AI/migration/workflow-origin not."""

from __future__ import annotations

import ast
from dataclasses import fields
from pathlib import Path

import pytest

from tests.dbutil import REPO_ROOT

pytestmark = pytest.mark.sai_i03

CONTENT_ROOT = REPO_ROOT / "src" / "aieos" / "domains" / "content"
AUDIT_HELPER = CONTENT_ROOT / "application" / "audit.py"
UOW = CONTENT_ROOT / "infrastructure" / "persistence" / "uow.py"
ADAPTER = CONTENT_ROOT / "infrastructure" / "persistence" / "audit_repository.py"
AI_SERVICE = CONTENT_ROOT / "application" / "ai_materialization.py"
MIGRATION_SERVICE = CONTENT_ROOT / "application" / "migration_import.py"
ROUTES = CONTENT_ROOT / "api" / "v1" / "routes.py"
MIGRATIONS = REPO_ROOT / "migrations" / "versions"
BOUNDARY_DOC = REPO_ROOT / "docs" / "GCI-I04-NON-PRODUCTION-MUTATION-BOUNDARY.md"

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
    "tosd090002_teaching_work_remediation_audit.py",
]


def _mentions(path: Path, needles: tuple[str, ...]) -> list[str]:
    text = path.read_text(encoding="utf-8")
    return [n for n in needles if n in text]


def test_migration_head_unchanged_no_saii030001() -> None:
    versions = sorted(p.name for p in MIGRATIONS.glob("*.py") if p.name != "__init__.py")
    assert versions == _EXPECTED_MIGRATIONS
    for path in MIGRATIONS.rglob("*.py"):
        body = path.read_text(encoding="utf-8")
        assert "saii030001" not in body
        assert "saii010001" not in body


def test_content_uow_exposes_insert_only_audit_on_same_connection_wiring() -> None:
    uow = UOW.read_text(encoding="utf-8")
    assert "ContentSecurityMutationAuditRepository(self._connection)" in uow
    assert "self.audit =" in uow
    adapter = ADAPTER.read_text(encoding="utf-8")
    assert "def insert(" in adapter
    assert "def get(" not in adapter
    assert "def list(" not in adapter
    assert "def search(" not in adapter
    assert "def update(" not in adapter
    assert "def delete(" not in adapter
    assert "def commit(" not in adapter
    assert "def rollback(" not in adapter
    assert "PersistenceOperationFailed" in adapter
    assert "SecurityAuditPersistenceError" in adapter


def test_canonical_helper_and_resource_types() -> None:
    helper = AUDIT_HELPER.read_text(encoding="utf-8")
    assert "insert_required_content_audit" in helper
    assert 'RESOURCE_CONTENT = "content.content"' in helper
    assert 'RESOURCE_CONTENT_VERSION = "content.content_version"' in helper
    assert 'RESOURCE_REVIEW_DECISION = "content.review_decision"' in helper
    assert 'RESOURCE_PUBLICATION = "content.publication"' in helper
    for forbidden in (
        "content.aggregate",
        'RESOURCE_REVIEW = "content.review"',
        "content.publish_record",
        "INSERT INTO security.audit_records",
    ):
        assert forbidden not in helper


def test_api_mutations_wire_audit_ai_and_migration_also_wired_in_i04() -> None:
    """SAI-I03 wired API; SAI-I04 advances AI/migration (see sai_i04 architecture)."""
    in_uow = (CONTENT_ROOT / "application" / "in_uow.py").read_text(encoding="utf-8")
    assert "insert_required_content_audit" in in_uow
    create = (CONTENT_ROOT / "application" / "create.py").read_text(encoding="utf-8")
    assert "create_content_in_uow" in create
    for name in ("http_append.py", "review.py", "publish.py"):
        text = (CONTENT_ROOT / "application" / name).read_text(encoding="utf-8")
        assert "insert_required_content_audit" in text, name
    ai = AI_SERVICE.read_text(encoding="utf-8")
    migration = MIGRATION_SERVICE.read_text(encoding="utf-8")
    assert "materialize_ai_version_in_uow" in ai
    assert "insert_required_content_audit" in migration
    doc = BOUNDARY_DOC.read_text(encoding="utf-8")
    assert "SAI-I04" in doc
    assert "content.ai.materialize" in doc
    assert "content.migration.import" in doc
    assert "SAI-I05" in doc


def test_no_audit_http_api() -> None:
    routes = ROUTES.read_text(encoding="utf-8")
    assert "api_mutation_audit_provenance" in routes
    for needle in ("/audit", "GET /security/audit", "audit_record_id"):
        assert needle not in routes
    # Client must not supply channel/trace/delegation via body fields in OpenAPI path handlers
    tree = ast.parse(routes)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name.startswith("content_"):
            src = ast.get_source_segment(routes, node) or ""
            assert "execution_channel" not in src or "api_mutation_audit_provenance" in src


def test_mutation_event_and_trusted_security_context_unchanged() -> None:
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


def test_no_workflow_activity_audit_channel_in_content() -> None:
    """WORKFLOW_ACTIVITY remains unused by Content through SAI-I04."""
    hits: list[str] = []
    for path in CONTENT_ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "WORKFLOW_ACTIVITY" in text or "SecurityAuditExecutionChannel.WORKFLOW" in text:
            hits.append(str(path.relative_to(REPO_ROOT)))
    assert hits == []


def test_application_services_do_not_emit_raw_security_sql() -> None:
    for path in (CONTENT_ROOT / "application").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "INSERT INTO security.audit_records" not in text
        assert "security.audit_records" not in text


def test_boundary_doc_still_non_production() -> None:
    doc = BOUNDARY_DOC.read_text(encoding="utf-8")
    assert "NOT AUTHORIZED" in doc or "NON-PRODUCTION" in doc
    assert "SAI-I05" in doc or "adversarial" in doc.lower()
    assert _mentions(BOUNDARY_DOC, ("content.ai.materialize", "migration")) != []

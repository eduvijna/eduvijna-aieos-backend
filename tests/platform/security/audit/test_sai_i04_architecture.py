"""SAI-I04 architecture: AI/migration audit wired; workflow-origin N/A."""

from __future__ import annotations

import ast
from dataclasses import fields
from pathlib import Path

import pytest

from tests.dbutil import REPO_ROOT

pytestmark = pytest.mark.sai_i04

CONTENT_ROOT = REPO_ROOT / "src" / "aieos" / "domains" / "content"
SRC_ROOT = REPO_ROOT / "src"
AUDIT_HELPER = CONTENT_ROOT / "application" / "audit.py"
AI_SERVICE = CONTENT_ROOT / "application" / "ai_materialization.py"
MIGRATION_SERVICE = CONTENT_ROOT / "application" / "migration_import.py"
APPEND_SERVICE = CONTENT_ROOT / "application" / "services.py"
WORKFLOW = (
    REPO_ROOT
    / "src"
    / "aieos"
    / "platform"
    / "workflows"
    / "temporal"
    / "content_review.py"
)
BOUNDARY_DOC = REPO_ROOT / "docs" / "GCI-I04-NON-PRODUCTION-MUTATION-BOUNDARY.md"
MIGRATIONS = REPO_ROOT / "migrations" / "versions"

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
]


def test_migration_head_unchanged_no_saii040001() -> None:
    versions = sorted(p.name for p in MIGRATIONS.glob("*.py") if p.name != "__init__.py")
    assert versions == _EXPECTED_MIGRATIONS
    for path in MIGRATIONS.rglob("*.py"):
        body = path.read_text(encoding="utf-8")
        assert "saii030001" not in body
        assert "saii040001" not in body


def test_ai_and_migration_wire_required_audit() -> None:
    ai = AI_SERVICE.read_text(encoding="utf-8")
    migration = MIGRATION_SERVICE.read_text(encoding="utf-8")
    assert "insert_required_content_audit" in ai
    assert "CONTENT_AI_MATERIALIZE" in ai
    assert "audit_provenance" in ai
    assert "insert_required_content_audit" in migration
    assert "CONTENT_MIGRATION_IMPORT" in migration
    assert "audit_provenance" in migration
    helper = AUDIT_HELPER.read_text(encoding="utf-8")
    assert "ai_materialization_audit_provenance" in helper
    assert "migration_audit_provenance" in helper
    assert "AI_MATERIALIZATION" in helper
    assert "MIGRATION" in helper


def test_api_actions_still_wired() -> None:
    for name in ("create.py", "http_append.py", "review.py", "publish.py"):
        text = (CONTENT_ROOT / "application" / name).read_text(encoding="utf-8")
        assert "insert_required_content_audit" in text, name


def test_no_workflow_activity_audit_in_content_implementation() -> None:
    hits: list[str] = []
    for path in CONTENT_ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "WORKFLOW_ACTIVITY" in text:
            hits.append(str(path.relative_to(REPO_ROOT)))
    assert hits == []


def test_content_review_workflow_is_process_truth_only() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "@workflow.run" in text
    assert "@workflow.signal" in text
    assert "@workflow.query" in text
    assert "@activity.defn" not in text
    assert "execute_activity" not in text
    assert "insert_required_content_audit" not in text
    assert "ContentUnitOfWork" not in text
    tree = ast.parse(text)
    class_names = {
        node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
    }
    assert "ContentReviewWorkflowV1" in class_names


def test_no_new_temporal_activity_or_workflow_mutation() -> None:
    temporal_root = REPO_ROOT / "src" / "aieos" / "platform" / "workflows" / "temporal"
    for path in temporal_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "@activity.defn" not in text
        assert "workflow.execute_activity" not in text


def test_direct_append_service_has_no_production_src_call_site() -> None:
    """AppendContentVersionService is test/domain foundation, not product runtime."""
    import re

    hits: list[str] = []
    pattern = re.compile(r"(?<![A-Za-z])AppendContentVersionService\(")
    for path in SRC_ROOT.rglob("*.py"):
        if path.name == "services.py":
            continue
        text = path.read_text(encoding="utf-8")
        if pattern.search(text):
            hits.append(str(path.relative_to(REPO_ROOT)))
    assert hits == []
    services = APPEND_SERVICE.read_text(encoding="utf-8")
    assert "class AppendContentVersionService" in services
    assert "Not a product-facing API entrypoint" in services


def test_direct_append_not_labeled_migration_import() -> None:
    services = APPEND_SERVICE.read_text(encoding="utf-8")
    assert "CONTENT_MIGRATION_IMPORT" not in services
    assert "content.migration.import" not in services
    assert "insert_required_content_audit" not in services


def test_failed_migration_evidence_authority_separated() -> None:
    doc = BOUNDARY_DOC.read_text(encoding="utf-8")
    assert "migration_import_records" in doc
    assert "FAILED" in doc
    assert "not" in doc.lower()
    migration = MIGRATION_SERVICE.read_text(encoding="utf-8")
    # FAILED finalization path must not insert success audit action string directly
    assert "CONTENT_MIGRATION_IMPORT" in migration
    # success audit only via insert_required_content_audit (import + one call site)
    assert migration.count("insert_required_content_audit(") == 1
    assert "_record_failure" in migration
    assert "insert_required_content_audit" not in migration.split("def _record_failure")[1]


def test_ai_provenance_authority_separated() -> None:
    doc = BOUNDARY_DOC.read_text(encoding="utf-8")
    assert "AIGenerationProvenanceV1" in doc
    assert "SecurityMutationAuditRecord" in doc
    ai = AI_SERVICE.read_text(encoding="utf-8")
    assert "command.provenance" in ai
    assert "generation_run_ref" not in AUDIT_HELPER.read_text(encoding="utf-8")


def test_future_workflow_rule_documented() -> None:
    doc = BOUNDARY_DOC.read_text(encoding="utf-8")
    assert "WORKFLOW_ACTIVITY" in doc
    assert "Future workflow rule" in doc or "future Temporal Activity" in doc
    assert "SAI-I05" in doc
    assert "NOT AUTHORIZED" in doc


def test_no_raw_security_sql_in_ai_or_migration() -> None:
    for path in (AI_SERVICE, MIGRATION_SERVICE):
        text = path.read_text(encoding="utf-8")
        assert "INSERT INTO security.audit_records" not in text
        assert "security.audit_records" not in text


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


def test_no_audit_http_or_product_ai_migration_routes() -> None:
    routes = (CONTENT_ROOT / "api" / "v1" / "routes.py").read_text(encoding="utf-8")
    assert "/audit" not in routes
    assert "audit_record_id" not in routes
    assert "actions/materialize" not in routes
    assert "actions/import" not in routes
    assert "/migrate" not in routes

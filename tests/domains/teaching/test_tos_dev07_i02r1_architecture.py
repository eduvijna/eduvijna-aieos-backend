"""TOS-DEV07-I02R1 architecture / vocabulary unit proofs (no PostgreSQL)."""

from __future__ import annotations

from pathlib import Path

import pytest

from aieos.platform.runtime.readiness import EXPECTED_ALEMBIC_HEAD
from aieos.platform.security.audit.actions import (
    SecurityAuditAction,
    is_teaching_create_action,
    is_teaching_increment_action,
)
from aieos.platform.security.audit.persistence.models import audit_records_table
from tools.dev.constants import EXPECTED_ALEMBIC_HEAD as DEV_EXPECTED_ALEMBIC_HEAD
from tools.release.common import EXPECTED_MIGRATION_HEAD, EXPECTED_OPENAPI_SHA256

pytestmark = pytest.mark.tos_dev07_i02r1

REPO_ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS = REPO_ROOT / "migrations" / "versions"
MIGRATION = MIGRATIONS / "tosd070002_teaching_execution_audit.py"

_EXECUTION_ACTIONS = (
    "teaching.execution.start",
    "teaching.execution.complete",
    "teaching.execution.cancel",
    "teaching.execution.observation.create",
    "teaching.execution.observation.correct",
)


def test_head_constants_and_chain() -> None:
    assert EXPECTED_ALEMBIC_HEAD == "tosd080001"
    assert EXPECTED_MIGRATION_HEAD == "tosd080001"
    assert DEV_EXPECTED_ALEMBIC_HEAD == "tosd080001"
    assert EXPECTED_OPENAPI_SHA256 == (
        "7D7D0E7C7115667757A31CFEB5474F7498ECC7198FB812DE5EF14A0E9F2D289A"
    )
    text_002 = MIGRATION.read_text(encoding="utf-8")
    assert 'revision: str = "tosd070002"' in text_002
    assert 'down_revision: str | None = "tosd070001"' in text_002
    assert "CREATE TABLE teaching." not in text_002
    assert "ALTER TABLE teaching." not in text_002
    assert "DROP TABLE" not in text_002
    text_001 = (MIGRATIONS / "tosd070001_teaching_executions.py").read_text(
        encoding="utf-8"
    )
    assert 'revision: str = "tosd070001"' in text_001
    assert 'down_revision: str | None = "tosd060002"' in text_001
    versions = sorted(
        p.name for p in MIGRATIONS.glob("*.py") if p.name != "__init__.py"
    )
    assert versions[-1] == "tosd080001_classroom_assessments.py"


def test_python_action_families() -> None:
    assert is_teaching_create_action(SecurityAuditAction.TEACHING_ASSIGNMENT_CREATE)
    assert is_teaching_create_action(SecurityAuditAction.TEACHING_EXECUTION_START)
    assert is_teaching_create_action(
        SecurityAuditAction.TEACHING_EXECUTION_OBSERVATION_CREATE
    )
    assert is_teaching_increment_action(SecurityAuditAction.TEACHING_EXECUTION_COMPLETE)
    assert is_teaching_increment_action(SecurityAuditAction.TEACHING_EXECUTION_CANCEL)
    assert is_teaching_increment_action(
        SecurityAuditAction.TEACHING_EXECUTION_OBSERVATION_CORRECT
    )
    assert not is_teaching_create_action(
        SecurityAuditAction.TEACHING_EXECUTION_COMPLETE
    )
    assert not is_teaching_increment_action(
        SecurityAuditAction.TEACHING_EXECUTION_START
    )


def test_sqlalchemy_mapping_mirrors_closed_vocabulary() -> None:
    action_constraint = next(
        c
        for c in audit_records_table.constraints
        if c.name == "ck_audit_records_action"
    )
    sql = str(action_constraint.sqltext)
    for action in _EXECUTION_ACTIONS:
        assert action in sql
    assert "teaching.assignment.create" in sql
    assert "teaching.execution.foo" not in sql
    assert "teaching.execution.observation.delete" not in sql
    assert "teaching.execution.create" not in sql
    semantics = next(
        c
        for c in audit_records_table.constraints
        if c.name == "ck_audit_records_revision_semantics"
    )
    semantics_sql = str(semantics.sqltext)
    assert "teaching.execution.start" in semantics_sql
    assert "teaching.execution.observation.create" in semantics_sql
    assert "teaching.execution.complete" in semantics_sql

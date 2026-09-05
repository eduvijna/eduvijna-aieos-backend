"""TOS-DEV09-I02 architecture and frozen-contract guards."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from aieos.domains.teaching.api.v1.models import (
    RemediationTeachingWorkCreateRequest,
)
from aieos.platform.idempotency.models import (
    TEACHING_WORK_FROM_CLASSROOM_ASSESSMENT_CREATE_V1,
)
from aieos.platform.runtime.readiness import EXPECTED_ALEMBIC_HEAD
from aieos.platform.security.audit.actions import SecurityAuditAction
from tools.release.common import EXPECTED_MIGRATION_HEAD, EXPECTED_OPENAPI_SHA256

pytestmark = pytest.mark.tos_dev09_i02

ROOT = Path(__file__).resolve().parents[3]
TEACHING = ROOT / "src" / "aieos" / "domains" / "teaching"
MIGRATIONS = ROOT / "migrations" / "versions"
MIGRATION = MIGRATIONS / "tosd090002_teaching_work_remediation_audit.py"
OPENAPI = ROOT / "contracts" / "openapi" / "aieos-v1.json"
ASSESSMENT_SOURCE = (
    ROOT / "src" / "aieos" / "platform" / "runtime" / "remediation_assessment_source.py"
)


def test_current_head_and_only_i02_migration() -> None:
    assert EXPECTED_ALEMBIC_HEAD == "tosd090002"
    assert EXPECTED_MIGRATION_HEAD == "tosd090002"
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'revision: str = "tosd090002"' in source
    assert 'down_revision: str | None = "tosd090001"' in source
    assert list(MIGRATIONS.glob("tosd090003*.py")) == []


def test_openapi_contains_dedicated_endpoint_and_digest_is_frozen() -> None:
    digest = hashlib.sha256(OPENAPI.read_bytes()).hexdigest().upper()
    assert digest == EXPECTED_OPENAPI_SHA256
    contract = OPENAPI.read_text(encoding="utf-8")
    assert "/api/v1/teaching/works/from-classroom-assessment" in contract
    assert "teaching_work_from_classroom_assessment_create" in contract


def test_teaching_has_no_assessment_infrastructure_dependency() -> None:
    bridge_importers = []
    for path in TEACHING.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        if "aieos.domains.assessment.infrastructure" in source:
            bridge_importers.append(path.relative_to(TEACHING))
    assert bridge_importers == []


def test_i02_files_exclude_deferred_scope() -> None:
    paths = [
        MIGRATION,
        TEACHING / "application" / "remediation_create.py",
        ASSESSMENT_SOURCE,
    ]
    forbidden = (
        "learner_id",
        "student_id",
        "mastery",
        "TeacherMemory",
        "temporalio",
        "nats",
        "improve.created",
    )
    for path in paths:
        source = path.read_text(encoding="utf-8").lower()
        for needle in forbidden:
            assert needle.lower() not in source, f"{path.name}:{needle}"


def test_generic_create_still_rejects_remediation_intent() -> None:
    source = (TEACHING / "application" / "create.py").read_text(encoding="utf-8")
    assert "REMEDIATE_CLASS" in source
    assert "Assessment-origin create" in source


def test_request_model_exposes_only_teacher_supplied_fields() -> None:
    assert set(RemediationTeachingWorkCreateRequest.model_fields) == {
        "assessment_id",
        "expected_assessment_aggregate_revision",
        "goal_text",
        "target_date",
        "locale",
        "subject",
        "topic",
    }
    forbidden = {
        "class_result_note",
        "intent_type",
        "teacher_principal_id",
        "class_ref",
        "class_label",
        "tenant_id",
        "initiating_teacher_principal_id",
    }
    assert forbidden.isdisjoint(RemediationTeachingWorkCreateRequest.model_fields)


def test_idempotency_operation_and_audit_action_are_exact() -> None:
    assert (
        TEACHING_WORK_FROM_CLASSROOM_ASSESSMENT_CREATE_V1
        == "teaching_work_from_classroom_assessment_create.v1"
    )
    assert (
        SecurityAuditAction.TEACHING_WORK_REMEDIATION_CREATE.value
        == "teaching.work.remediation.create"
    )

"""TOS-DEV06-I03 — architecture guards for TeachingAssignment application/API."""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import pytest

from aieos.platform.events.constants import (
    EMITTED_TEACHING_EVENT_TYPES,
    EVENT_TEACHING_ASSIGNMENT_CREATED_V1,
    PRODUCTION_EVENT_PUBLISH_PREFIXES,
)
from aieos.platform.idempotency.models import (
    TEACHING_ASSIGNMENT_CANCEL_V1,
    TEACHING_ASSIGNMENT_CLOSE_V1,
    TEACHING_ASSIGNMENT_CREATE_V1,
    TEACHING_ASSIGNMENT_DUE_UPDATE_V1,
)
from aieos.platform.runtime.activation import (
    FROZEN_API_MUTATION_OPERATION_IDS,
    READ_ONLY_OPERATION_IDS,
)
from aieos.platform.security.audit.actions import (
    SecurityAuditAction,
    is_teaching_audit_action,
    is_teaching_create_action,
    is_teaching_increment_action,
)
from tools.release.common import EXPECTED_OPENAPI_SHA256

pytestmark = pytest.mark.tos_dev06_i03

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC = REPO_ROOT / "src" / "aieos"
TEACHING = SRC / "domains" / "teaching"
OPENAPI = REPO_ROOT / "contracts" / "openapi" / "aieos-v1.json"
MIGRATION = REPO_ROOT / "migrations" / "versions" / "tosd060001_teaching_assignments.py"


class TestI03ArchitectureGuards:
    def test_platform_idempotency_and_audit_actions(self) -> None:
        assert TEACHING_ASSIGNMENT_CREATE_V1 == "teaching_assignment_create.v1"
        assert TEACHING_ASSIGNMENT_DUE_UPDATE_V1 == "teaching_assignment_due_update.v1"
        assert SecurityAuditAction.TEACHING_ASSIGNMENT_CREATE.value == (
            "teaching.assignment.create"
        )
        assert is_teaching_create_action(SecurityAuditAction.TEACHING_ASSIGNMENT_CREATE)
        assert is_teaching_increment_action(
            SecurityAuditAction.TEACHING_ASSIGNMENT_CLOSE
        )
        assert is_teaching_audit_action(SecurityAuditAction.TEACHING_ASSIGNMENT_CANCEL)

    def test_event_constants_and_publish_prefixes(self) -> None:
        assert EVENT_TEACHING_ASSIGNMENT_CREATED_V1 in EMITTED_TEACHING_EVENT_TYPES
        assert PRODUCTION_EVENT_PUBLISH_PREFIXES[1] == "io.eduvijna.aieos.teaching."

    def test_migration_amends_audit_without_new_revision(self) -> None:
        text = MIGRATION.read_text(encoding="utf-8")
        assert 'revision: str = "tosd060001"' in text
        assert "teaching.assignment.create" in text
        assert "AUDIT_UPGRADE_STATEMENTS" in text

    def test_openapi_contains_assignment_operations(self) -> None:
        schema = json.loads(OPENAPI.read_text(encoding="utf-8"))
        paths = schema["paths"]
        assert "/api/v1/teaching/assignments" in paths
        post = paths["/api/v1/teaching/assignments"]["post"]
        assert post["operationId"] == "teaching_assignment_create"
        digest = hashlib.sha256(OPENAPI.read_bytes()).hexdigest().upper()
        assert digest == EXPECTED_OPENAPI_SHA256

    def test_activation_inventory_includes_assignment_operations(self) -> None:
        for op in (
            "teaching_assignment_create",
            "teaching_assignment_due_update",
            "teaching_assignment_close",
            "teaching_assignment_cancel",
        ):
            assert op in FROZEN_API_MUTATION_OPERATION_IDS
        for op in ("teaching_assignment_get", "teaching_assignment_list"):
            assert op in READ_ONLY_OPERATION_IDS

    def test_application_layer_wires_outbox_and_audit_on_uow(self) -> None:
        uow = TEACHING / "infrastructure" / "persistence" / "uow.py"
        tree = ast.parse(uow.read_text(encoding="utf-8"), filename=str(uow))
        source = uow.read_text(encoding="utf-8")
        assert "SqlAlchemyOutboxRepository" in source
        assert "TeachingSecurityMutationAuditRepository" in source
        assert "SqlAlchemyContentAssignmentEligibilityAdapter" in source
        assert tree is not None

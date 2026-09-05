"""TOS-DEV07-I02 — architecture guards for Teach composition application/API."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from aieos.platform.events.constants import EMITTED_TEACHING_EVENT_TYPES
from aieos.platform.runtime.activation import (
    FROZEN_API_MUTATION_OPERATION_IDS,
    READ_ONLY_OPERATION_IDS,
)
from aieos.platform.runtime.readiness import EXPECTED_ALEMBIC_HEAD
from tools.release.common import EXPECTED_MIGRATION_HEAD, EXPECTED_OPENAPI_SHA256

pytestmark = pytest.mark.tos_dev07_i02

REPO_ROOT = Path(__file__).resolve().parents[3]
OPENAPI = REPO_ROOT / "contracts" / "openapi" / "aieos-v1.json"
MIGRATIONS = REPO_ROOT / "migrations" / "versions"

EXECUTION_MUTATION_OPS = (
    "teaching_execution_start",
    "teaching_execution_complete",
    "teaching_execution_cancel",
    "teaching_execution_observation_create",
    "teaching_execution_observation_correct",
)
EXECUTION_READ_OPS = (
    "teaching_execution_get",
    "teaching_execution_list",
    "teacher_os_teach_context_get",
)


class TestI02ArchitectureGuards:
    def test_alembic_head_still_tosd070002(self) -> None:
        assert EXPECTED_ALEMBIC_HEAD == "tosd090002"
        assert EXPECTED_MIGRATION_HEAD == "tosd090002"
        versions = sorted(
            p.name for p in MIGRATIONS.glob("*.py") if p.name != "__init__.py"
        )
        assert versions[-1].startswith("tosd090002_")

    def test_openapi_has_execution_and_teach_context_ops(self) -> None:
        schema = json.loads(OPENAPI.read_text(encoding="utf-8"))
        paths = schema["paths"]
        assert "/api/v1/teaching/executions" in paths
        assert paths["/api/v1/teaching/executions"]["post"]["operationId"] == (
            "teaching_execution_start"
        )
        assert paths["/api/v1/teaching/executions"]["get"]["operationId"] == (
            "teaching_execution_list"
        )
        get_path = "/api/v1/teaching/executions/{execution_id}"
        assert paths[get_path]["get"]["operationId"] == "teaching_execution_get"
        assert (
            paths["/api/v1/teacher-os/teach/context"]["get"]["operationId"]
            == "teacher_os_teach_context_get"
        )
        for op in EXECUTION_MUTATION_OPS + EXECUTION_READ_OPS:
            assert op in json.dumps(schema)

    def test_no_observation_event_types_in_emitted_set(self) -> None:
        for etype in EMITTED_TEACHING_EVENT_TYPES:
            assert "observation" not in etype
        assert "io.eduvijna.aieos.teaching.execution.started.v1" in (
            EMITTED_TEACHING_EVENT_TYPES
        )
        assert "io.eduvijna.aieos.teaching.execution.completed.v1" in (
            EMITTED_TEACHING_EVENT_TYPES
        )
        assert "io.eduvijna.aieos.teaching.execution.cancelled.v1" in (
            EMITTED_TEACHING_EVENT_TYPES
        )

    def test_activation_op_ids_present(self) -> None:
        for op in EXECUTION_MUTATION_OPS:
            assert op in FROZEN_API_MUTATION_OPERATION_IDS
        for op in EXECUTION_READ_OPS:
            assert op in READ_ONLY_OPERATION_IDS

    def test_openapi_digest_matches_expected(self) -> None:
        digest = hashlib.sha256(OPENAPI.read_bytes()).hexdigest().upper()
        assert digest == EXPECTED_OPENAPI_SHA256

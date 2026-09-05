"""TOS-DEV09-I01 — architecture guards for remediation origin persistence."""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import pytest

from aieos.platform.runtime.readiness import EXPECTED_ALEMBIC_HEAD
from tools.release.common import EXPECTED_MIGRATION_HEAD, EXPECTED_OPENAPI_SHA256

pytestmark = pytest.mark.tos_dev09_i01

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = REPO_ROOT / "src" / "aieos"
TEACHING_ROOT = SRC_ROOT / "domains" / "teaching"
MIGRATIONS = REPO_ROOT / "migrations" / "versions"
MIGRATION = MIGRATIONS / "tosd090001_remediation_work_origin.py"
OPENAPI = REPO_ROOT / "contracts" / "openapi" / "aieos-v1.json"
ROUTES = TEACHING_ROOT / "api" / "v1" / "routes.py"


def _sql_literals(path: Path) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return "\n".join(
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    )


def _py_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*.py") if p.is_file()]


class TestI01ArchitectureGuards:
    def test_current_alembic_head_tosd090001(self) -> None:
        assert EXPECTED_ALEMBIC_HEAD == "tosd090002"
        assert EXPECTED_MIGRATION_HEAD == "tosd090002"
        text = MIGRATION.read_text(encoding="utf-8")
        assert 'revision: str = "tosd090001"' in text
        assert 'down_revision: str | None = "tosd080002"' in text
        versions = sorted(
            p.name for p in MIGRATIONS.glob("*.py") if p.name != "__init__.py"
        )
        assert versions[-1] == "tosd090002_teaching_work_remediation_audit.py"

    def test_no_learner_mastery_memory_note_observation_fields(self) -> None:
        sql = _sql_literals(MIGRATION).lower()
        for needle in (
            "learner_id",
            "student_id",
            "class_result_note",
            "private_execution_note",
            "teacher_memory",
            "create table teaching.mastery",
        ):
            assert needle not in sql
        domain = TEACHING_ROOT / "domain" / "remediation_origin.py"
        text = domain.read_text(encoding="utf-8")
        for needle in (
            "learner_id",
            "student_id",
            "class_result_note",
            "observation_body",
            "mastery",
            "TeacherMemory",
        ):
            assert needle not in text

    def test_no_improve_aggregate_or_lifecycle(self) -> None:
        sql = _sql_literals(MIGRATION).lower()
        for needle in (
            "create table teaching.improvements",
            "create table teaching.remediations",
            "create table teaching.teaching_intents",
        ):
            assert needle not in sql
        assert "CREATE TABLE teaching.work_remediation_origins" in _sql_literals(
            MIGRATION
        )
        domain_text = (
            TEACHING_ROOT / "domain" / "remediation_origin.py"
        ).read_text(encoding="utf-8")
        assert "ImproveLifecycle" not in domain_text
        assert "DRAFT" not in domain_text
        assert "SUGGESTED" not in domain_text

    def test_no_improve_nats_or_temporal(self) -> None:
        for path in _py_files(TEACHING_ROOT / "domain"):
            text = path.read_text(encoding="utf-8")
            for needle in (
                "improve.created.v1",
                "remediation.created.v1",
                "temporalio",
                "nats",
            ):
                assert needle not in text, f"{path.name}:{needle}"
        migration = MIGRATION.read_text(encoding="utf-8")
        assert "nats" not in migration.lower()
        assert "temporal" not in migration.lower()

    def test_no_generic_improvement_resource(self) -> None:
        routes = ROUTES.read_text(encoding="utf-8")
        assert "/improvements" not in routes
        openapi = OPENAPI.read_text(encoding="utf-8")
        assert "/improvements" not in openapi

    def test_openapi_digest_unchanged(self) -> None:
        digest = hashlib.sha256(OPENAPI.read_bytes()).hexdigest().upper()
        assert digest == EXPECTED_OPENAPI_SHA256
        assert digest == (
            "B4326D43A213D7831F2AAD8E77A2CEC6BA70B800B4C62EFC52D5B8DFC07CB4D9"
        )

    def test_generic_create_guards_remediate_class(self) -> None:
        create = (
            TEACHING_ROOT / "application" / "create.py"
        ).read_text(encoding="utf-8")
        assert "REMEDIATE_CLASS" in create
        assert "Assessment-origin create" in create

    def test_commit_time_pair_enforcement_present(self) -> None:
        sql = _sql_literals(MIGRATION)
        assert "CONSTRAINT TRIGGER" in sql
        assert "DEFERRABLE INITIALLY DEFERRED" in sql
        assert "enforce_remediation_work_has_origin" in sql
        assert "enforce_origin_work_is_remediate_class" in sql
        assert "reject_work_remediation_origin_mutation" in sql
        assert "reject_teaching_work_intent_type_mutation" in sql
        assert "BEFORE UPDATE OF intent_type ON teaching.works" in sql

"""TOS-DEV08-I01 — architecture guards for ClassroomAssessment persistence."""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import pytest

from aieos.domains.assessment.infrastructure.persistence.models import (
    classroom_assessments_table,
)
from aieos.platform.runtime.readiness import (
    EXPECTED_ALEMBIC_HEAD,
    _CONTENT_OWNED_SCHEMAS,
)
from tools.release.common import EXPECTED_MIGRATION_HEAD, EXPECTED_OPENAPI_SHA256

pytestmark = pytest.mark.tos_dev08_i01

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = REPO_ROOT / "src" / "aieos"
ASSESSMENT_ROOT = SRC_ROOT / "domains" / "assessment"
MIGRATIONS = REPO_ROOT / "migrations" / "versions"
MIGRATION = MIGRATIONS / "tosd080001_classroom_assessments.py"
OPENAPI = REPO_ROOT / "contracts" / "openapi" / "aieos-v1.json"
READINESS = SRC_ROOT / "platform" / "runtime" / "readiness.py"


def _sql_literals(path: Path) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return "\n".join(
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    )


def _py_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*.py") if p.is_file()]


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


class TestI01ArchitectureGuards:
    def test_current_alembic_head_tosd080001(self) -> None:
        assert EXPECTED_ALEMBIC_HEAD == "tosd080002"
        assert EXPECTED_MIGRATION_HEAD == "tosd080002"
        text = MIGRATION.read_text(encoding="utf-8")
        assert 'revision: str = "tosd080001"' in text
        assert 'down_revision: str | None = "tosd070002"' in text
        versions = sorted(
            p.name for p in MIGRATIONS.glob("*.py") if p.name != "__init__.py"
        )
        assert versions[-1] == "tosd080002_classroom_assessment_audit.py"

    def test_assessment_schema_in_readiness_ownership(self) -> None:
        assert "assessment" in _CONTENT_OWNED_SCHEMAS
        readiness = READINESS.read_text(encoding="utf-8")
        assert "'assessment'" in readiness
        assert "assessment" in readiness

    def test_domain_does_not_import_teaching_aggregates(self) -> None:
        domain_root = ASSESSMENT_ROOT / "domain"
        forbidden = (
            "aieos.domains.teaching",
            "domains.teaching",
            "TeachingWork",
            "TeachingAssignment",
            "TeachingExecution",
        )
        for path in _py_files(domain_root):
            text = path.read_text(encoding="utf-8")
            imports = _imports(path)
            for name in imports:
                assert "domains.teaching" not in name, f"{path.name} imports {name}"
            for needle in forbidden:
                if needle.startswith("Teaching") and needle in text:
                    raise AssertionError(f"{path.name} references {needle}")

    def test_no_learner_specific_field_names(self) -> None:
        sql = _sql_literals(MIGRATION).lower()
        for needle in (
            "learner_id",
            "student_id",
            "learnerref",
            "studentref",
            "assessment_attempts",
            "assessment_submissions",
            "learner_results",
        ):
            assert needle not in sql
        assert "create table assessment.mastery" not in sql
        for path in _py_files(ASSESSMENT_ROOT):
            text = path.read_text(encoding="utf-8")
            for needle in ("learner_id", "student_id", "LearnerRef", "StudentRef"):
                assert needle not in text, f"{path.name}:{needle}"

    def test_no_attempt_submission_or_mastery_tables(self) -> None:
        sql = _sql_literals(MIGRATION).lower()
        for needle in (
            "create table assessment.assessment_attempts",
            "create table assessment.assessment_submissions",
            "create table assessment.mastery",
        ):
            assert needle not in sql
        assert "CREATE TABLE assessment.classroom_assessments" in _sql_literals(
            MIGRATION
        )

    def test_no_cross_domain_foreign_keys(self) -> None:
        assert classroom_assessments_table.foreign_key_constraints == set()
        sql = _sql_literals(MIGRATION).lower()
        assert "references content." not in sql
        assert "references teaching." not in sql
        assert "constraint fk_" not in sql

    def test_no_assessment_events_or_temporal(self) -> None:
        for path in _py_files(ASSESSMENT_ROOT):
            text = path.read_text(encoding="utf-8")
            for needle in (
                "assessment.recorded.v1",
                "assessment.corrected.v1",
                "assessment.voided.v1",
                "temporalio",
                "nats",
                "PRODUCTION_EVENT_PUBLISH_PREFIXES",
            ):
                assert needle not in text, f"{path.name}:{needle}"

    def test_i01_domain_and_persistence_remain_api_free(self) -> None:
        """I01 delivered domain + persistence only; HTTP lives under api/ (I02)."""
        for root_name in ("domain",):
            for path in _py_files(ASSESSMENT_ROOT / root_name):
                text = path.read_text(encoding="utf-8")
                assert "APIRouter" not in text
                assert "fastapi" not in text.lower()
        persistence = ASSESSMENT_ROOT / "infrastructure" / "persistence"
        for path in _py_files(persistence):
            text = path.read_text(encoding="utf-8")
            assert "APIRouter" not in text
        assert (ASSESSMENT_ROOT / "api").is_dir()

    def test_openapi_digest_matches_release_pin(self) -> None:
        digest = hashlib.sha256(OPENAPI.read_bytes()).hexdigest().upper()
        assert digest == EXPECTED_OPENAPI_SHA256
        assert digest == (
            "824B389D6D4EDB2EA5D8ED3A9E5411087B566DFDCA09C2AB0CD4FDED51C4D89D"
        )

    def test_i01_domain_has_no_audit_or_idempotency_wiring(self) -> None:
        """I01 domain/persistence must not own audit/idempotency; I02 application does."""
        for path in _py_files(ASSESSMENT_ROOT / "domain"):
            text = path.read_text(encoding="utf-8")
            for needle in (
                "assessment.classroom.record",
                "assessment.classroom.correct",
                "assessment.classroom.void",
                "Idempotency-Key",
                "idempotency_records",
            ):
                assert needle not in text, f"{path.name}:{needle}"
        migration = MIGRATION.read_text(encoding="utf-8")
        assert "assessment.classroom.record" not in migration
        assert "idempotency" not in migration.lower()

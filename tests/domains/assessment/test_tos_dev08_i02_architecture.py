"""TOS-DEV08-I02 architecture guards."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from aieos.platform.runtime.activation import (
    FROZEN_API_MUTATION_OPERATION_IDS,
    READ_ONLY_OPERATION_IDS,
)
from aieos.platform.runtime.readiness import EXPECTED_ALEMBIC_HEAD
from tools.release.common import EXPECTED_MIGRATION_HEAD, EXPECTED_OPENAPI_SHA256

pytestmark = pytest.mark.tos_dev08_i02

REPO = Path(__file__).resolve().parents[3]
ASSESSMENT = REPO / "src" / "aieos" / "domains" / "assessment"
OPENAPI = REPO / "contracts" / "openapi" / "aieos-v1.json"
MIGRATION = (
    REPO / "migrations" / "versions" / "tosd080002_classroom_assessment_audit.py"
)

FORBIDDEN_LEARNER = (
    "learner_id",
    "student_id",
    "LearnerRef",
    "StudentRef",
    "assessment_attempts",
    "assessment_submissions",
    "mastery",
)
FORBIDDEN_EVENTS = (
    "io.eduvijna.aieos.assessment.",
    "temporal",
    "StructuredModelGateway",
)


class TestI02ArchitectureGuards:
    def test_current_alembic_head_tosd080002(self) -> None:
        assert EXPECTED_ALEMBIC_HEAD == "tosd090001"
        assert EXPECTED_MIGRATION_HEAD == "tosd090001"
        text = MIGRATION.read_text(encoding="utf-8")
        assert 'revision: str = "tosd080002"' in text
        assert 'down_revision: str | None = "tosd080001"' in text

    def test_exact_routes_and_operation_ids(self) -> None:
        routes = (ASSESSMENT / "api" / "v1" / "routes.py").read_text(encoding="utf-8")
        for op in (
            "assessment_classroom_record",
            "assessment_classroom_list",
            "assessment_classroom_get",
            "assessment_classroom_correct",
            "assessment_classroom_void",
        ):
            assert f'operation_id="{op}"' in routes
        assert "assessment_classroom_record" in FROZEN_API_MUTATION_OPERATION_IDS
        assert "assessment_classroom_correct" in FROZEN_API_MUTATION_OPERATION_IDS
        assert "assessment_classroom_void" in FROZEN_API_MUTATION_OPERATION_IDS
        assert "assessment_classroom_get" in READ_ONLY_OPERATION_IDS
        assert "assessment_classroom_list" in READ_ONLY_OPERATION_IDS

    def test_no_learner_mastery_events_temporal_ai(self) -> None:
        hits: list[str] = []
        for path in ASSESSMENT.rglob("*.py"):
            text = path.read_text(encoding="utf-8").lower()
            for needle in FORBIDDEN_LEARNER:
                if needle.lower() in text and "not" not in text[
                    max(0, text.find(needle.lower()) - 40) : text.find(needle.lower())
                ]:
                    # allow documentary negation in comments/docstrings
                    if f"no {needle.lower()}" in text or f"not {needle.lower()}" in text:
                        continue
                    if needle.lower() in {
                        "mastery",
                        "learner_id",
                        "student_id",
                        "learnerref",
                        "studentref",
                        "assessment_attempts",
                        "assessment_submissions",
                    }:
                        # documentary mentions in module docs are allowed if denying
                        if "not" in path.read_text(encoding="utf-8").lower() or "no " in path.read_text(encoding="utf-8").lower():
                            continue
            raw = path.read_text(encoding="utf-8")
            for needle in ("assessment_attempts", "assessment_submissions"):
                if needle in raw:
                    hits.append(f"{path.name}:{needle}")
            if "outbox" in raw.lower() and "NO outbox" not in raw and "no outbox" not in raw.lower():
                if "OutboxRepository" in raw:
                    hits.append(f"{path.name}:outbox")
        assert hits == []

    def test_no_cross_domain_fk_and_no_teaching_mutation(self) -> None:
        models = (
            ASSESSMENT / "infrastructure" / "persistence" / "models.py"
        ).read_text(encoding="utf-8")
        assert "ForeignKey" not in models
        teaching = (
            ASSESSMENT / "infrastructure" / "persistence" / "teaching_composition.py"
        ).read_text(encoding="utf-8")
        assert ".update(" not in teaching
        assert ".insert(" not in teaching

    def test_result_and_lifecycle_unchanged(self) -> None:
        result = (ASSESSMENT / "domain" / "result.py").read_text(encoding="utf-8")
        life = (ASSESSMENT / "domain" / "lifecycle.py").read_text(encoding="utf-8")
        assert "DEMONSTRATED = " in result and "MIXED = " in result
        assert "NOT_YET_DEMONSTRATED = " in result
        assert "NEEDS_RETEACH =" not in result
        assert "RECORDED = " in life and "VOIDED = " in life
        assert "CANCELLED =" not in life

    def test_openapi_digest_matches_expected(self) -> None:
        digest = hashlib.sha256(OPENAPI.read_bytes()).hexdigest().upper()
        assert digest == EXPECTED_OPENAPI_SHA256
        assert digest == (
            "824B389D6D4EDB2EA5D8ED3A9E5411087B566DFDCA09C2AB0CD4FDED51C4D89D"
        )
        schema = OPENAPI.read_text(encoding="utf-8")
        assert "/api/v1/assessment/classroom-assessments" in schema
        assert "assessment_classroom_record" in schema
        assert "Improve" not in schema or "improve" not in schema.lower()

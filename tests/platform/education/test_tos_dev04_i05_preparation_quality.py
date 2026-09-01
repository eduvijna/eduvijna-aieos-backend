"""TOS-DEV04-I05 preparation educational quality + coherence baseline proofs."""

from __future__ import annotations

import ast

import pytest

from aieos.domains.education.application.models import PreparationArtifactPayloadsV1
from aieos.domains.education.application.preparation_artifacts import (
    build_preparation_artifact_payloads,
)
from aieos.domains.education.preparation_kit_v1 import PreparationKitV1
from aieos.domains.education.worksheet_v1 import LearningObjectiveV1
from aieos.platform.education.preparation_quality_baseline import (
    evaluate_preparation_educational_quality_v1,
)
from aieos.platform.education.quality_baseline import (
    EducationalQualityStatus,
    evaluate_educational_quality_baseline_v1,
    find_unsupported_alignment_claim,
)
from tests.dbutil import REPO_ROOT
from tests.domains.teaching.worksheet_fixtures import valid_worksheet_payload

pytestmark = pytest.mark.tos_dev04_i05

_PREP_EQ = (
    REPO_ROOT / "src" / "aieos" / "platform" / "education" / "preparation_quality_baseline.py"
)
_QUALITY = REPO_ROOT / "src" / "aieos" / "platform" / "education" / "quality_baseline.py"

_PREP_CODES = (
    "schema_valid",
    "shared_objectives_present",
    "lesson_plan_objectives_mapped",
    "worksheet_objectives_mapped",
    "quiz_objectives_mapped",
    "homework_objectives_mapped",
    "question_identifier_integrity",
    "answer_key_complete",
    "answer_key_reference_integrity",
    "cross_artifact_objective_consistency",
    "cross_artifact_topic_consistency",
    "unsupported_alignment_claim_absent",
    "teacher_notes_present",
)

_WORKSHEET_PREFIXED = (
    "worksheet_schema_valid",
    "worksheet_learning_objectives_present",
    "worksheet_question_count_valid",
    "worksheet_objective_mapping_complete",
    "worksheet_answer_key_complete",
    "worksheet_bloom_labels_valid",
    "worksheet_bloom_distribution_baseline",
    "worksheet_difficulty_distribution_baseline",
    "worksheet_unsupported_alignment_claim_absent",
)


def _question(
    *,
    qid: str,
    objective_ids: list[str] | None = None,
    answer: str = "1/2",
    explanation: str | None = None,
    bloom: str = "understand",
    difficulty: str = "medium",
    question_type: str = "short_answer",
) -> dict[str, object]:
    options = ["1/2", "1/3", "1/4", "2/3"] if question_type == "multiple_choice" else []
    if options and answer not in options:
        answer = options[0]
    return {
        "id": qid,
        "prompt": f"Prompt for {qid}",
        "question_type": question_type,
        "difficulty": difficulty,
        "bloom_level": bloom,
        "objective_ids": objective_ids or ["obj-1"],
        "options": options,
        "answer": answer,
        "explanation": explanation or f"Explanation for {qid}",
        "visual_description": None,
    }


def _worksheet_questions(count: int = 6) -> list[dict[str, object]]:
    blooms = ["remember", "understand", "apply"]
    difficulties = ["easy", "medium", "hard"]
    questions: list[dict[str, object]] = []
    for index in range(count):
        qtype = ("multiple_choice", "short_answer", "true_false")[index % 3]
        answer = "1/2"
        if qtype == "true_false":
            answer = "true"
        elif qtype == "multiple_choice":
            answer = ["1/2", "1/3", "1/4", "2/3"][index % 4]
        questions.append(
            _question(
                qid=f"ws-{index + 1}",
                objective_ids=["obj-1" if index % 2 == 0 else "obj-2"],
                answer=answer,
                bloom=blooms[index % len(blooms)],
                difficulty=difficulties[index % len(difficulties)],
                question_type=qtype,
            )
        )
    return questions


def _pass_kit_payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "title": "Fractions preparation kit",
        "teacher_summary": "Coherent kit for tomorrow's fractions class.",
        "shared_learning_objectives": [
            {"id": "obj-1", "text": "Identify fraction parts of a whole"},
            {"id": "obj-2", "text": "Compare simple fractions"},
        ],
        "lesson_plan": {
            "title": "Fractions lesson",
            "objective_ids": ["obj-1", "obj-2"],
            "materials": ["fraction tiles", "chart paper"],
            "opening": "Activate prior knowledge with a pizza model.",
            "sections": [
                {
                    "id": "sec-1",
                    "title": "Explore",
                    "objective_ids": ["obj-1"],
                    "teacher_actions": "Demonstrate halves and quarters.",
                    "learner_actions": "Build models with tiles.",
                    "estimated_minutes": 12,
                }
            ],
            "closure": "Summarize equivalent fractions.",
            "formative_check": "Ask one exit ticket question.",
        },
        "worksheet": {
            "title": "Fractions worksheet",
            "instructions": "Answer all questions carefully.",
            "questions": _worksheet_questions(6),
        },
        "quick_quiz": {
            "title": "Fractions exit quiz",
            "instructions": "Answer independently.",
            "questions": [
                _question(qid="q1", bloom="understand", difficulty="easy"),
                _question(
                    qid="quiz-2",
                    objective_ids=["obj-2"],
                    bloom="apply",
                    difficulty="medium",
                ),
            ],
        },
        "homework": {
            "title": "Fractions homework",
            "instructions": "Complete at home.",
            "questions": [
                _question(
                    qid="hw-1",
                    objective_ids=["obj-2"],
                    bloom="understand",
                    difficulty="medium",
                )
            ],
        },
        "teacher_notes": {
            "title": "Teacher preparation notes",
            "notes": ["Watch for half/quarter confusion.", "Keep quiz under five minutes."],
        },
    }
    base.update(overrides)
    return base


def pass_artifacts() -> PreparationArtifactPayloadsV1:
    kit = PreparationKitV1.model_validate(_pass_kit_payload())
    return build_preparation_artifact_payloads(kit)


def _by_code(result) -> dict[str, bool]:
    return {c.code: c.passed for c in result.checks}


def _codes(result) -> list[str]:
    return [c.code for c in result.checks]


class TestPassFixture:
    def test_coherent_kit_passes(self) -> None:
        artifacts = pass_artifacts()
        # Inherited DEV03 must pass standalone first
        ws_eq = evaluate_educational_quality_baseline_v1(artifacts.worksheet)
        assert ws_eq.status is EducationalQualityStatus.PASS

        result = evaluate_preparation_educational_quality_v1(artifacts)
        assert result.status is EducationalQualityStatus.PASS
        assert all(c.passed for c in result.checks)
        assert _codes(result)[: len(_PREP_CODES)] == list(_PREP_CODES)
        assert _codes(result)[len(_PREP_CODES) :] == list(_WORKSHEET_PREFIXED)

    def test_check_order_deterministic(self) -> None:
        a = evaluate_preparation_educational_quality_v1(pass_artifacts())
        b = evaluate_preparation_educational_quality_v1(pass_artifacts())
        assert _codes(a) == _codes(b)
        assert a.as_summary() == b.as_summary()

    def test_cross_component_same_question_id_legal(self) -> None:
        payload = _pass_kit_payload()
        payload["worksheet"]["questions"][0]["id"] = "q1"  # type: ignore[index]
        artifacts = build_preparation_artifact_payloads(
            PreparationKitV1.model_validate(payload)
        )
        result = evaluate_preparation_educational_quality_v1(artifacts)
        assert result.status is EducationalQualityStatus.PASS
        assert _by_code(result)["question_identifier_integrity"] is True


class TestFailureMatrix:
    def test_schema_valid_fails_on_mutated_artifact(self) -> None:
        artifacts = pass_artifacts()
        artifacts.lesson_plan.title = "   "
        result = evaluate_preparation_educational_quality_v1(artifacts)
        assert result.status is EducationalQualityStatus.FAIL
        assert _by_code(result)["schema_valid"] is False
        assert _by_code(result)["shared_objectives_present"] is False
        assert result.checks[1].explanation == "skipped: final artifact schema invalid"

    def test_empty_objectives_fail(self) -> None:
        artifacts = pass_artifacts()
        artifacts.quiz.learning_objectives = []
        result = evaluate_preparation_educational_quality_v1(artifacts)
        assert result.status is EducationalQualityStatus.FAIL
        codes = _by_code(result)
        assert codes["schema_valid"] is False or codes["shared_objectives_present"] is False

    def test_lesson_plan_unknown_objective_fails(self) -> None:
        artifacts = pass_artifacts()
        artifacts.lesson_plan.objective_ids = ["missing"]
        result = evaluate_preparation_educational_quality_v1(artifacts)
        assert result.status is EducationalQualityStatus.FAIL
        codes = _by_code(result)
        assert codes["schema_valid"] is False or codes["lesson_plan_objectives_mapped"] is False

    def test_worksheet_unknown_objective_fails(self) -> None:
        artifacts = pass_artifacts()
        artifacts.worksheet.questions[0].objective_ids = ["missing"]
        result = evaluate_preparation_educational_quality_v1(artifacts)
        assert result.status is EducationalQualityStatus.FAIL
        codes = _by_code(result)
        assert codes["schema_valid"] is False or codes["worksheet_objectives_mapped"] is False

    def test_quiz_unknown_objective_fails(self) -> None:
        artifacts = pass_artifacts()
        artifacts.quiz.questions[0].objective_ids = ["missing"]
        result = evaluate_preparation_educational_quality_v1(artifacts)
        assert result.status is EducationalQualityStatus.FAIL
        codes = _by_code(result)
        assert codes["schema_valid"] is False or codes["quiz_objectives_mapped"] is False

    def test_homework_unknown_objective_fails(self) -> None:
        artifacts = pass_artifacts()
        artifacts.homework.questions[0].objective_ids = ["missing"]
        result = evaluate_preparation_educational_quality_v1(artifacts)
        assert result.status is EducationalQualityStatus.FAIL
        codes = _by_code(result)
        assert codes["schema_valid"] is False or codes["homework_objectives_mapped"] is False

    def test_duplicate_question_id_within_component_fails(self) -> None:
        artifacts = pass_artifacts()
        artifacts.quiz.questions[1].id = artifacts.quiz.questions[0].id
        result = evaluate_preparation_educational_quality_v1(artifacts)
        assert result.status is EducationalQualityStatus.FAIL
        codes = _by_code(result)
        assert (
            codes["schema_valid"] is False
            or codes["question_identifier_integrity"] is False
        )

    def test_missing_answer_key_entry_fails(self) -> None:
        artifacts = pass_artifacts()
        artifacts.answer_key.entries = list(artifacts.answer_key.entries[:-1])
        result = evaluate_preparation_educational_quality_v1(artifacts)
        assert result.status is EducationalQualityStatus.FAIL
        # AnswerKeyV1 still valid with fewer entries; completeness must catch it
        if _by_code(result)["schema_valid"]:
            assert _by_code(result)["answer_key_complete"] is False

    def test_extra_answer_key_entry_fails(self) -> None:
        artifacts = pass_artifacts()
        extra = artifacts.answer_key.entries[0].model_copy(
            update={"source_question_id": "extra-q"}
        )
        artifacts.answer_key.entries = list(artifacts.answer_key.entries) + [extra]
        result = evaluate_preparation_educational_quality_v1(artifacts)
        assert result.status is EducationalQualityStatus.FAIL
        if _by_code(result)["schema_valid"]:
            assert _by_code(result)["answer_key_complete"] is False

    def test_answer_key_answer_drift_fails(self) -> None:
        artifacts = pass_artifacts()
        entry = artifacts.answer_key.entries[0]
        artifacts.answer_key.entries[0] = entry.model_copy(update={"answer": "DRIFTED"})
        result = evaluate_preparation_educational_quality_v1(artifacts)
        assert result.status is EducationalQualityStatus.FAIL
        assert _by_code(result)["schema_valid"] is True
        assert _by_code(result)["answer_key_reference_integrity"] is False

    def test_answer_key_explanation_drift_fails(self) -> None:
        artifacts = pass_artifacts()
        entry = artifacts.answer_key.entries[0]
        artifacts.answer_key.entries[0] = entry.model_copy(
            update={"explanation": "DRIFTED-EXPLAIN"}
        )
        result = evaluate_preparation_educational_quality_v1(artifacts)
        assert result.status is EducationalQualityStatus.FAIL
        assert _by_code(result)["schema_valid"] is True
        assert _by_code(result)["answer_key_reference_integrity"] is False

    def test_objective_text_changed_fails_consistency(self) -> None:
        artifacts = pass_artifacts()
        objs = list(artifacts.quiz.learning_objectives)
        objs[0] = LearningObjectiveV1(id=objs[0].id, text="MUTATED OBJECTIVE TEXT")
        artifacts.quiz.learning_objectives = objs
        result = evaluate_preparation_educational_quality_v1(artifacts)
        assert result.status is EducationalQualityStatus.FAIL
        assert _by_code(result)["schema_valid"] is True
        assert _by_code(result)["cross_artifact_objective_consistency"] is False
        assert _by_code(result)["cross_artifact_topic_consistency"] is False

    def test_objective_order_changed_fails_consistency(self) -> None:
        artifacts = pass_artifacts()
        objs = list(artifacts.quiz.learning_objectives)
        artifacts.quiz.learning_objectives = list(reversed(objs))
        result = evaluate_preparation_educational_quality_v1(artifacts)
        assert result.status is EducationalQualityStatus.FAIL
        assert _by_code(result)["cross_artifact_objective_consistency"] is False
        assert _by_code(result)["cross_artifact_topic_consistency"] is False

    def test_cbse_in_teacher_notes_fails_global_alignment(self) -> None:
        artifacts = pass_artifacts()
        artifacts.teacher_notes.notes = [
            "Use this as the official CBSE-aligned version."
        ]
        # Worksheet alone still passes DEV03
        ws_eq = evaluate_educational_quality_baseline_v1(artifacts.worksheet)
        assert ws_eq.status is EducationalQualityStatus.PASS
        assert _by_code(ws_eq)["unsupported_alignment_claim_absent"] is True

        result = evaluate_preparation_educational_quality_v1(artifacts)
        assert result.status is EducationalQualityStatus.FAIL
        assert _by_code(result)["unsupported_alignment_claim_absent"] is False
        assert _by_code(result)["worksheet_unsupported_alignment_claim_absent"] is True

    def test_cbse_in_lesson_plan_fails(self) -> None:
        artifacts = pass_artifacts()
        artifacts.lesson_plan.opening = "This is the official CBSE unit opener."
        result = evaluate_preparation_educational_quality_v1(artifacts)
        assert result.status is EducationalQualityStatus.FAIL
        assert _by_code(result)["unsupported_alignment_claim_absent"] is False

    def test_blank_teacher_notes_fail(self) -> None:
        artifacts = pass_artifacts()
        artifacts.teacher_notes.notes = ["   "]
        result = evaluate_preparation_educational_quality_v1(artifacts)
        assert result.status is EducationalQualityStatus.FAIL
        codes = _by_code(result)
        assert codes["schema_valid"] is False or codes["teacher_notes_present"] is False

    def test_single_difficulty_inherits_dev03_fail(self) -> None:
        payload = _pass_kit_payload()
        for q in payload["worksheet"]["questions"]:  # type: ignore[index]
            q["difficulty"] = "easy"
        artifacts = build_preparation_artifact_payloads(
            PreparationKitV1.model_validate(payload)
        )
        result = evaluate_preparation_educational_quality_v1(artifacts)
        assert result.status is EducationalQualityStatus.FAIL
        assert _by_code(result)["worksheet_difficulty_distribution_baseline"] is False

    def test_single_bloom_inherits_dev03_fail(self) -> None:
        payload = _pass_kit_payload()
        for q in payload["worksheet"]["questions"]:  # type: ignore[index]
            q["bloom_level"] = "remember"
        artifacts = build_preparation_artifact_payloads(
            PreparationKitV1.model_validate(payload)
        )
        result = evaluate_preparation_educational_quality_v1(artifacts)
        assert result.status is EducationalQualityStatus.FAIL
        assert _by_code(result)["worksheet_bloom_distribution_baseline"] is False


class TestSummarySafety:
    def test_as_summary_sanitized(self) -> None:
        result = evaluate_preparation_educational_quality_v1(pass_artifacts())
        summary = result.as_summary()
        assert set(summary.keys()) == {"status", "checks"}
        blob = str(summary).lower()
        for forbidden in (
            "api_key",
            "authorization",
            "chain-of-thought",
            "raw_response",
            "prompt for ws-",
        ):
            assert forbidden not in blob
        for check in summary["checks"]:  # type: ignore[index]
            assert set(check.keys()) == {"code", "passed", "explanation"}


class TestSharedAlignmentHelper:
    def test_find_helper_public(self) -> None:
        assert find_unsupported_alignment_claim("plain text") is None
        assert find_unsupported_alignment_claim("official CBSE unit") == "CBSE"

    def test_dev03_semantics_unchanged(self) -> None:
        ok = evaluate_educational_quality_baseline_v1(valid_worksheet_payload())
        assert ok.status is EducationalQualityStatus.PASS
        assert [c.code for c in ok.checks] == [
            "schema_valid",
            "learning_objectives_present",
            "question_count_valid",
            "objective_mapping_complete",
            "answer_key_complete",
            "bloom_labels_valid",
            "bloom_distribution_baseline",
            "difficulty_distribution_baseline",
            "unsupported_alignment_claim_absent",
        ]
        bad = evaluate_educational_quality_baseline_v1(
            valid_worksheet_payload(include_alignment_claim=True)
        )
        assert bad.status is EducationalQualityStatus.FAIL
        assert _by_code(bad)["unsupported_alignment_claim_absent"] is False


class TestArchitectureGuards:
    def test_no_forbidden_imports(self) -> None:
        for path in (_PREP_EQ, _QUALITY):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            imports: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.update(a.name.split(".")[0] for a in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports.add(node.module.split(".")[0])
            for forbidden in (
                "openai",
                "anthropic",
                "temporalio",
                "langchain",
                "llama_index",
                "crewai",
                "autogen",
                "mcp",
            ):
                assert forbidden not in imports

    def test_no_gateway_content_generation_run(self) -> None:
        source = _PREP_EQ.read_text(encoding="utf-8").lower()
        for forbidden in (
            "structuredmodelgateway",
            "generate_structured",
            "contentunitofwork",
            "createaipreparation",
            "generationrun",
            "openai",
            "temporal",
            "llm",
            "embedding",
            "cosine",
        ):
            assert forbidden not in source

    def test_capability_untouched(self) -> None:
        cap = (
            REPO_ROOT
            / "src"
            / "aieos"
            / "domains"
            / "education"
            / "application"
            / "generate_preparation_kit.py"
        ).read_text(encoding="utf-8")
        assert "evaluate_preparation_educational_quality" not in cap
        assert "preparation_quality_baseline" not in cap

    def test_no_new_migration(self) -> None:
        versions = sorted((REPO_ROOT / "migrations" / "versions").glob("*.py"))
        assert versions[-1].name.startswith("tosd060002_")
        assert not any(p.name.startswith("tosd040002_") for p in versions)

    def test_app_factory_untouched(self) -> None:
        factory = (
            REPO_ROOT / "src" / "aieos" / "development" / "app_factory.py"
        ).read_text(encoding="utf-8")
        assert "evaluate_preparation_educational_quality" not in factory

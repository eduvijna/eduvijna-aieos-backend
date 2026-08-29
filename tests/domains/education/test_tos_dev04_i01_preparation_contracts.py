"""TOS-DEV04-I01 PreparationKitV1 contract adversarial tests."""

from __future__ import annotations

import ast

import pytest
from pydantic import ValidationError

from aieos.domains.education.preparation_kit_v1 import (
    AnswerKeyEntryV1,
    AnswerKeySourceArtifactKind,
    AnswerKeyV1,
    PreparationKitV1,
)
from aieos.domains.education.worksheet_v1 import QuestionType
from tests.dbutil import REPO_ROOT

pytestmark = pytest.mark.tos_dev04_i01

_EDUCATION_ROOT = REPO_ROOT / "src" / "aieos" / "domains" / "education"
_FORBIDDEN_IMPORTS = (
    "openai",
    "temporalio",
    "langchain",
    "llama_index",
    "crewai",
    "autogen",
    "semantic_kernel",
)


def _question(
    *,
    qid: str,
    objective_ids: list[str] | None = None,
    answer: str = "1/2",
    options: list[str] | None = None,
    question_type: str = "short_answer",
) -> dict[str, object]:
    if options is None:
        options = [] if question_type != "multiple_choice" else ["1/2", "1/3", "1/4"]
    if question_type == "multiple_choice" and answer not in options:
        answer = options[0]
    return {
        "id": qid,
        "prompt": f"Prompt for {qid}",
        "question_type": question_type,
        "difficulty": "easy",
        "bloom_level": "understand",
        "objective_ids": objective_ids or ["obj-1"],
        "options": options,
        "answer": answer,
        "explanation": f"Explanation for {qid}",
        "visual_description": None,
    }


def _lesson_plan_section(*, sid: str = "sec-1", objective_ids: list[str] | None = None) -> dict[str, object]:
    return {
        "id": sid,
        "title": "Explore",
        "objective_ids": objective_ids or ["obj-1"],
        "teacher_actions": "Demonstrate with visuals.",
        "learner_actions": "Observe and discuss.",
        "estimated_minutes": 10,
    }


def valid_preparation_kit_payload(**overrides) -> dict[str, object]:
    base: dict[str, object] = {
        "title": "Photosynthesis preparation kit",
        "teacher_summary": "Coherent kit for tomorrow's class on photosynthesis.",
        "shared_learning_objectives": [
            {"id": "obj-1", "text": "Explain how plants make food"},
            {"id": "obj-2", "text": "Identify inputs and outputs of photosynthesis"},
        ],
        "lesson_plan": {
            "title": "Photosynthesis lesson",
            "objective_ids": ["obj-1", "obj-2"],
            "materials": ["Chart paper", "Leaf samples"],
            "opening": "Recall prior knowledge about plants.",
            "sections": [_lesson_plan_section()],
            "closure": "Summarize the process.",
            "formative_check": "Thumbs up/down on key idea.",
        },
        "worksheet": {
            "title": "Photosynthesis worksheet",
            "instructions": "Answer all questions.",
            "questions": [_question(qid="ws-1"), _question(qid="ws-2", objective_ids=["obj-2"])],
        },
        "quick_quiz": {
            "title": "Exit quiz",
            "instructions": "Answer quickly.",
            "questions": [_question(qid="quiz-1")],
        },
        "homework": {
            "title": "Photosynthesis homework",
            "instructions": "Complete for next class.",
            "questions": [_question(qid="hw-1", objective_ids=["obj-2"])],
        },
        "teacher_notes": {
            "title": "Teacher preparation notes",
            "notes": ["Review leaf diagrams before class.", "Keep quiz under five minutes."],
        },
    }
    base.update(overrides)
    return base


def test_valid_preparation_kit_parses() -> None:
    kit = PreparationKitV1.model_validate(valid_preparation_kit_payload())
    assert kit.title.startswith("Photosynthesis")
    assert "answer_key" not in PreparationKitV1.model_fields


def test_extra_field_rejected() -> None:
    payload = valid_preparation_kit_payload(extra_field="nope")
    with pytest.raises(ValidationError):
        PreparationKitV1.model_validate(payload)


def test_answer_key_field_rejected_as_extra() -> None:
    payload = valid_preparation_kit_payload(
        answer_key={"title": "Key", "entries": []},
    )
    with pytest.raises(ValidationError):
        PreparationKitV1.model_validate(payload)


def test_duplicate_shared_objective_ids_rejected() -> None:
    payload = valid_preparation_kit_payload(
        shared_learning_objectives=[
            {"id": "obj-1", "text": "One"},
            {"id": "obj-1", "text": "Duplicate"},
        ]
    )
    with pytest.raises(ValidationError):
        PreparationKitV1.model_validate(payload)


def test_blank_objective_id_rejected() -> None:
    payload = valid_preparation_kit_payload(
        shared_learning_objectives=[{"id": " ", "text": "Bad"}]
    )
    with pytest.raises(ValidationError):
        PreparationKitV1.model_validate(payload)


@pytest.mark.parametrize(
    "component,mutator",
    [
        (
            "worksheet",
            lambda p: p["worksheet"]["questions"][0].update({"objective_ids": ["missing"]}),
        ),
        (
            "quick_quiz",
            lambda p: p["quick_quiz"]["questions"][0].update({"objective_ids": ["missing"]}),
        ),
        (
            "homework",
            lambda p: p["homework"]["questions"][0].update({"objective_ids": ["missing"]}),
        ),
        (
            "lesson_plan",
            lambda p: p["lesson_plan"].update({"objective_ids": ["missing"]}),
        ),
        (
            "lesson_plan_section",
            lambda p: p["lesson_plan"]["sections"][0].update({"objective_ids": ["missing"]}),
        ),
    ],
)
def test_unknown_objective_reference_rejected(component: str, mutator) -> None:
    payload = valid_preparation_kit_payload()
    mutator(payload)
    with pytest.raises(ValidationError):
        PreparationKitV1.model_validate(payload)


@pytest.mark.parametrize(
    "component,key",
    [
        ("worksheet", "worksheet"),
        ("quick_quiz", "quick_quiz"),
        ("homework", "homework"),
    ],
)
def test_duplicate_question_ids_rejected(component: str, key: str) -> None:
    payload = valid_preparation_kit_payload()
    payload[key]["questions"].append(payload[key]["questions"][0])  # type: ignore[index]
    with pytest.raises(ValidationError):
        PreparationKitV1.model_validate(payload)


def test_invalid_mcq_answer_rejected() -> None:
    payload = valid_preparation_kit_payload()
    payload["worksheet"]["questions"][0] = {  # type: ignore[index]
        "id": "ws-bad",
        "prompt": "Pick one",
        "question_type": QuestionType.MULTIPLE_CHOICE.value,
        "difficulty": "easy",
        "bloom_level": "understand",
        "objective_ids": ["obj-1"],
        "options": ["A", "B", "C"],
        "answer": "D",
        "explanation": "Because.",
        "visual_description": None,
    }
    with pytest.raises(ValidationError):
        PreparationKitV1.model_validate(payload)


def test_blank_answer_rejected() -> None:
    payload = valid_preparation_kit_payload()
    payload["worksheet"]["questions"][0]["answer"] = ""  # type: ignore[index]
    with pytest.raises(ValidationError):
        PreparationKitV1.model_validate(payload)


def test_answer_key_invalid_source_kind_rejected() -> None:
    with pytest.raises(ValidationError):
        AnswerKeyEntryV1.model_validate(
            {
                "source_artifact_kind": "lesson_plan",
                "source_question_id": "ws-1",
                "answer": "1/2",
                "explanation": "Because.",
            }
        )


def test_answer_key_duplicate_composite_reference_rejected() -> None:
    entry = {
        "source_artifact_kind": AnswerKeySourceArtifactKind.WORKSHEET.value,
        "source_question_id": "ws-1",
        "answer": "1/2",
        "explanation": "Because.",
    }
    with pytest.raises(ValidationError):
        AnswerKeyV1.model_validate(
            {
                "title": "Answer key",
                "entries": [entry, entry],
            }
        )


def test_architecture_abuse_no_forbidden_imports_in_contracts() -> None:
    target = _EDUCATION_ROOT / "preparation_kit_v1.py"
    tree = ast.parse(target.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])
    for forbidden in _FORBIDDEN_IMPORTS:
        assert forbidden not in imports


def test_preparation_contract_has_no_provider_payload_fields() -> None:
    source = (_EDUCATION_ROOT / "preparation_kit_v1.py").read_text(encoding="utf-8").lower()
    for forbidden in (
        "raw_response",
        "raw_prompt",
        "chain_of_thought",
        "generation_artifacts",
        "generation_validated_outputs",
        "preparationkit aggregate",
    ):
        assert forbidden not in source


def _kit_with_whitespace_objective_id() -> dict[str, object]:
    whitespace_id = "   "
    payload = valid_preparation_kit_payload()
    payload["shared_learning_objectives"] = [{"id": whitespace_id, "text": "Objective text"}]
    payload["lesson_plan"]["objective_ids"] = [whitespace_id]  # type: ignore[index]
    payload["lesson_plan"]["sections"][0]["objective_ids"] = [whitespace_id]  # type: ignore[index]
    for key in ("worksheet", "quick_quiz", "homework"):
        for question in payload[key]["questions"]:  # type: ignore[index]
            question["objective_ids"] = [whitespace_id]
    return payload


def test_duplicate_lesson_plan_section_ids_rejected() -> None:
    payload = valid_preparation_kit_payload()
    payload["lesson_plan"]["sections"] = [  # type: ignore[index]
        _lesson_plan_section(sid="sec-1"),
        _lesson_plan_section(sid="sec-1"),
    ]
    with pytest.raises(ValidationError):
        PreparationKitV1.model_validate(payload)


def test_whitespace_shared_objective_id_rejected_even_when_referenced() -> None:
    with pytest.raises(ValidationError) as exc_info:
        PreparationKitV1.model_validate(_kit_with_whitespace_objective_id())
    assert "shared learning objective id" in str(exc_info.value).lower()


def test_whitespace_shared_objective_text_rejected() -> None:
    payload = valid_preparation_kit_payload(
        shared_learning_objectives=[{"id": "obj-1", "text": "   "}]
    )
    with pytest.raises(ValidationError) as exc_info:
        PreparationKitV1.model_validate(payload)
    assert "shared learning objective text" in str(exc_info.value).lower()


@pytest.mark.parametrize(
    "component,key",
    [
        ("worksheet", "worksheet"),
        ("quiz", "quick_quiz"),
        ("homework", "homework"),
    ],
)
def test_whitespace_question_answer_rejected(component: str, key: str) -> None:
    payload = valid_preparation_kit_payload()
    payload[key]["questions"][0]["answer"] = "   "  # type: ignore[index]
    with pytest.raises(ValidationError) as exc_info:
        PreparationKitV1.model_validate(payload)
    assert f"{component} question answer" in str(exc_info.value).lower()


def test_whitespace_question_id_rejected() -> None:
    payload = valid_preparation_kit_payload()
    payload["worksheet"]["questions"][0]["id"] = "   "  # type: ignore[index]
    with pytest.raises(ValidationError) as exc_info:
        PreparationKitV1.model_validate(payload)
    assert "worksheet question id" in str(exc_info.value).lower()


def test_whitespace_answer_key_source_question_id_rejected() -> None:
    with pytest.raises(ValidationError) as exc_info:
        AnswerKeyEntryV1.model_validate(
            {
                "source_artifact_kind": AnswerKeySourceArtifactKind.WORKSHEET.value,
                "source_question_id": "   ",
                "answer": "1/2",
                "explanation": "Because.",
            }
        )
    assert "source_question_id" in str(exc_info.value).lower() or "blank" in str(exc_info.value).lower()


def test_whitespace_answer_key_answer_rejected() -> None:
    with pytest.raises(ValidationError) as exc_info:
        AnswerKeyEntryV1.model_validate(
            {
                "source_artifact_kind": AnswerKeySourceArtifactKind.WORKSHEET.value,
                "source_question_id": "ws-1",
                "answer": "   ",
                "explanation": "Because.",
            }
        )
    assert "answer" in str(exc_info.value).lower() or "blank" in str(exc_info.value).lower()


def test_whitespace_preparation_kit_title_rejected() -> None:
    payload = valid_preparation_kit_payload(title="   ")
    with pytest.raises(ValidationError) as exc_info:
        PreparationKitV1.model_validate(payload)
    assert "title" in str(exc_info.value).lower() or "blank" in str(exc_info.value).lower()

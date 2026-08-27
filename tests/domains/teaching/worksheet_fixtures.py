"""Shared helpers for valid WorksheetV1 fixtures and fake model gateways."""

from __future__ import annotations

from aieos.domains.education.worksheet_v1 import WorksheetV1


def valid_worksheet_payload(
    *,
    title: str = "Fractions Worksheet",
    include_alignment_claim: bool = False,
    question_count: int = 6,
    single_bloom: bool = False,
    single_difficulty: bool = False,
) -> dict[str, object]:
    summary = "Practice worksheet for visual fractions."
    if include_alignment_claim:
        summary = "CBSE aligned fractions practice."
    objectives = [
        {"id": "obj-1", "text": "Identify fraction parts of a whole"},
        {"id": "obj-2", "text": "Compare simple fractions"},
    ]
    blooms = ["remember", "understand", "apply"]
    difficulties = ["easy", "medium", "hard"]
    questions: list[dict[str, object]] = []
    for index in range(question_count):
        bloom = "remember" if single_bloom else blooms[index % len(blooms)]
        difficulty = "easy" if single_difficulty else difficulties[index % len(difficulties)]
        if index % 3 == 0:
            options = ["1/2", "1/3", "1/4", "2/3"]
            answer = options[index % len(options)]
            qtype = "multiple_choice"
        elif index % 3 == 1:
            options = []
            answer = "1/2"
            qtype = "short_answer"
        else:
            options = []
            answer = "true"
            qtype = "true_false"
        questions.append(
            {
                "id": f"q-{index + 1}",
                "prompt": f"Question {index + 1} about fractions",
                "question_type": qtype,
                "difficulty": difficulty,
                "bloom_level": bloom,
                "objective_ids": [objectives[index % 2]["id"]],
                "options": options,
                "answer": answer,
                "explanation": f"Explanation for question {index + 1}",
                "visual_description": None,
            }
        )
    return {
        "title": title,
        "teacher_summary": summary,
        "learning_objectives": objectives,
        "instructions": "Answer all questions carefully.",
        "questions": questions,
        "teacher_notes": "Review visual models before assigning.",
    }


def valid_worksheet_model(**kwargs) -> WorksheetV1:
    return WorksheetV1.model_validate(valid_worksheet_payload(**kwargs))

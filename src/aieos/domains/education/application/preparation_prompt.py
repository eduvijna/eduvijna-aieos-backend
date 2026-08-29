"""Prompt construction for education.generate_preparation_kit. No CoT / self-approval."""

from __future__ import annotations

from aieos.domains.education.application.models import PreparationKitGenerationInput

INSTRUCTIONS = """You create one coherent teacher-review preparation kit for classroom preparation.

Requirements:
- Produce one coherent preparation outcome that satisfies the stated Teaching Work goal.
- Use only the supplied class / subject / topic context; do not invent school facts.
- Do not invent learner names or any personal student data (PII).
- Do not claim verified curriculum or board alignment (no CBSE, ICSE, NCF, Cambridge, IB, or similar claims).
- shared_learning_objectives are canonical for the entire kit.
- lesson_plan, worksheet, quick_quiz, and homework objective references must use those shared objective IDs only.
- Question IDs must remain unique inside each component (worksheet, quick_quiz, homework).
- The worksheet must be convertible to authoritative WorksheetV1: include 6–12 questions.
- quick_quiz and homework must each contain at least one valid question.
- Every question must include authoritative answer and explanation fields.
- teacher_notes must be present.
- Do NOT generate answer_key. Answer Key is derived later by AIEOS; it is absent from model output.
- Conform exactly to the PreparationKitV1 structured output schema.
- Teacher review remains required before any learner use.

Cross-field validity (enforced after generation):
- Every shared learning objective id must be unique.
- Every question id must be unique within its component.
- Each question objective_ids entry must reference an id present in shared_learning_objectives.
- Lesson-plan and section objective_ids must reference shared learning objectives.
- multiple_choice: exactly 3–5 non-empty options; answer must exactly equal one option.
- short_answer and true_false: options must be an empty list.
- Use short stable machine identifiers for ids (for example obj1, q1).

Do not approve your own output.
Do not publish or mark the result as ready for learners.
Do not include chain-of-thought or hidden reasoning.
"""


def build_preparation_input_text(generation_input: PreparationKitGenerationInput) -> str:
    lines = [
        f"Educational goal: {generation_input.goal_text}",
        f"Target date: {generation_input.target_date.isoformat()}",
        f"Locale: {generation_input.locale}",
    ]
    if generation_input.class_label:
        lines.append(f"Class label: {generation_input.class_label}")
    if generation_input.subject:
        lines.append(f"Subject: {generation_input.subject}")
    if generation_input.topic:
        lines.append(f"Topic: {generation_input.topic}")
    lines.append(
        f"Source work: {generation_input.work_ref.resource_type}/"
        f"{generation_input.work_ref.resource_id}"
        f"@r{generation_input.work_ref.resource_revision}"
    )
    return "\n".join(lines)

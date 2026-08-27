"""Prompt construction for education.generate_worksheet. No CoT / self-approval."""

from __future__ import annotations

from aieos.domains.education.application.models import WorksheetGenerationInput

INSTRUCTIONS = """You create a teacher-review worksheet draft for classroom preparation.

Requirements:
- Satisfy the stated educational goal.
- Use class/subject/topic only when provided; do not invent school facts.
- Do not invent student names or personal student data.
- Do not claim verified curriculum or board alignment (no CBSE, ICSE, NCF, NCP, Cambridge, IB, or similar claims).
- Use plain age-appropriate language based only on the supplied context.
- Include complete teacher answer and explanation data for every question.
- Conform exactly to the WorksheetV1 structured output schema.
- This output will be reviewed by a teacher before any learner use.

Do not approve your own output.
Do not include chain-of-thought or hidden reasoning.
"""


def build_worksheet_input_text(generation_input: WorksheetGenerationInput) -> str:
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

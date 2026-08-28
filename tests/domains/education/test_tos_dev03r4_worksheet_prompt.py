"""TOS-DEV03R4 worksheet prompt cross-field and hygiene rules."""

from __future__ import annotations

import uuid
from datetime import date

import pytest

from aieos.domains.education.application.prompt import INSTRUCTIONS, build_worksheet_input_text
from aieos.domains.education.application.models import WorksheetGenerationInput
from aieos.platform.resources import ResourceRef

pytestmark = [pytest.mark.tos_dev03, pytest.mark.tos_dev03r4]


def _input() -> WorksheetGenerationInput:
    return WorksheetGenerationInput(
        goal_text="Introduce fractions with visual examples.",
        target_date=date(2026, 8, 29),
        locale="en-IN",
        class_label="Grade 5",
        subject="Mathematics",
        topic="Fractions",
        work_ref=ResourceRef("teaching.work", uuid.uuid7(), 1),
    )


class TestWorksheetPromptRules:
    def test_cross_field_constraints_present(self) -> None:
        text = INSTRUCTIONS.lower()
        assert "unique" in text
        assert "objective_ids" in text
        assert "multiple_choice" in text
        assert "short_answer" in text
        assert "true_false" in text
        assert "empty list" in text
        assert "obj1" in text or "obj1" in INSTRUCTIONS

    def test_no_pii_invention(self) -> None:
        assert "do not invent student names" in INSTRUCTIONS.lower()

    def test_no_curriculum_alignment_claims(self) -> None:
        lowered = INSTRUCTIONS.lower()
        assert "do not claim verified curriculum" in lowered
        assert "cbse" in lowered
        assert "alignment" in lowered

    def test_teacher_review_required(self) -> None:
        assert "reviewed by a teacher" in INSTRUCTIONS.lower()

    def test_no_chain_of_thought(self) -> None:
        lowered = INSTRUCTIONS.lower()
        assert "chain-of-thought" in lowered
        assert "hidden reasoning" in lowered

    def test_input_text_contains_only_supplied_context(self) -> None:
        rendered = build_worksheet_input_text(_input())
        assert "Grade 5" in rendered
        assert "Fractions" in rendered
        assert "teaching.work/" in rendered

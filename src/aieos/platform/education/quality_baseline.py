"""Educational Quality Baseline V1 — provider-independent post-model checks."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping, Sequence

from pydantic import ValidationError

from aieos.domains.education.worksheet_v1 import (
    BloomLevel,
    Difficulty,
    WorksheetV1,
)

_ALIGNMENT_TERMS = (
    "cbse",
    "icse",
    "ncf",
    "ncp",
    "cambridge",
    "ib",
)
_ALIGNMENT_RE = re.compile(
    r"\b(?:" + "|".join(_ALIGNMENT_TERMS) + r")\b",
    re.IGNORECASE,
)
_HIGHER_BLOOM = frozenset({BloomLevel.UNDERSTAND, BloomLevel.APPLY})


class EducationalQualityStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"


@dataclass(frozen=True, slots=True)
class EducationalQualityCheck:
    code: str
    passed: bool
    explanation: str


@dataclass(frozen=True, slots=True)
class EducationalQualityResult:
    status: EducationalQualityStatus
    checks: tuple[EducationalQualityCheck, ...]

    @property
    def passed(self) -> bool:
        return self.status is EducationalQualityStatus.PASS

    def as_summary(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "checks": [
                {
                    "code": check.code,
                    "passed": check.passed,
                    "explanation": check.explanation,
                }
                for check in self.checks
            ],
        }


def find_unsupported_alignment_claim(text: str) -> str | None:
    """Return the first unsupported curriculum/board claim token, if any.

    Shared public policy helper for DEV03 worksheet EQ and DEV04 preparation EQ.
    Case-insensitive whole-word match against the frozen alignment vocabulary.
    """
    if not isinstance(text, str) or not text:
        return None
    match = _ALIGNMENT_RE.search(text)
    if match is None:
        return None
    return match.group(0)


def _check(code: str, passed: bool, explanation: str) -> EducationalQualityCheck:
    return EducationalQualityCheck(code=code, passed=passed, explanation=explanation)


def _collect_text(worksheet: WorksheetV1) -> str:
    parts: list[str] = [
        worksheet.title,
        worksheet.teacher_summary,
        worksheet.instructions,
    ]
    if worksheet.teacher_notes:
        parts.append(worksheet.teacher_notes)
    for objective in worksheet.learning_objectives:
        parts.append(objective.text)
    for question in worksheet.questions:
        parts.extend(
            [
                question.prompt,
                question.answer,
                question.explanation,
                *(question.options or ()),
            ]
        )
        if question.visual_description:
            parts.append(question.visual_description)
    return "\n".join(parts)


def evaluate_educational_quality_baseline_v1(
    payload: Mapping[str, object] | WorksheetV1,
) -> EducationalQualityResult:
    """Run Educational Quality Baseline V1 after model output, before Content."""

    checks: list[EducationalQualityCheck] = []
    worksheet: WorksheetV1 | None = None

    if isinstance(payload, WorksheetV1):
        worksheet = payload
        checks.append(_check("schema_valid", True, "payload conforms to WorksheetV1"))
    else:
        try:
            worksheet = WorksheetV1.model_validate(dict(payload))
            checks.append(_check("schema_valid", True, "payload conforms to WorksheetV1"))
        except (ValidationError, TypeError, ValueError) as exc:
            checks.append(
                _check(
                    "schema_valid",
                    False,
                    f"payload failed WorksheetV1 validation: {type(exc).__name__}",
                )
            )
            # Remaining checks cannot run without a parsed worksheet.
            for code, explanation in (
                ("learning_objectives_present", "skipped: schema invalid"),
                ("question_count_valid", "skipped: schema invalid"),
                ("objective_mapping_complete", "skipped: schema invalid"),
                ("answer_key_complete", "skipped: schema invalid"),
                ("bloom_labels_valid", "skipped: schema invalid"),
                ("bloom_distribution_baseline", "skipped: schema invalid"),
                ("difficulty_distribution_baseline", "skipped: schema invalid"),
                ("unsupported_alignment_claim_absent", "skipped: schema invalid"),
            ):
                checks.append(_check(code, False, explanation))
            return EducationalQualityResult(
                status=EducationalQualityStatus.FAIL,
                checks=tuple(checks),
            )

    assert worksheet is not None

    checks.append(
        _check(
            "learning_objectives_present",
            len(worksheet.learning_objectives) >= 1,
            f"found {len(worksheet.learning_objectives)} learning objective(s)",
        )
    )

    count = len(worksheet.questions)
    checks.append(
        _check(
            "question_count_valid",
            6 <= count <= 12,
            f"question count is {count}; required range is 6–12",
        )
    )

    known_objectives = {obj.id for obj in worksheet.learning_objectives}
    mapping_ok = all(
        question.objective_ids and all(oid in known_objectives for oid in question.objective_ids)
        for question in worksheet.questions
    )
    checks.append(
        _check(
            "objective_mapping_complete",
            mapping_ok,
            "every question maps to at least one valid learning objective"
            if mapping_ok
            else "one or more questions lack valid objective mapping",
        )
    )

    answer_ok = all(
        question.answer.strip() and question.explanation.strip()
        for question in worksheet.questions
    )
    checks.append(
        _check(
            "answer_key_complete",
            answer_ok,
            "every question has a non-empty answer and explanation"
            if answer_ok
            else "one or more questions missing answer or explanation",
        )
    )

    bloom_ok = all(isinstance(q.bloom_level, BloomLevel) for q in worksheet.questions)
    checks.append(
        _check(
            "bloom_labels_valid",
            bloom_ok,
            "all bloom_level labels are recognized"
            if bloom_ok
            else "one or more bloom_level labels are invalid",
        )
    )

    bloom_levels = {q.bloom_level for q in worksheet.questions}
    has_higher = bool(bloom_levels & _HIGHER_BLOOM)
    bloom_dist_ok = len(bloom_levels) >= 2 and has_higher
    checks.append(
        _check(
            "bloom_distribution_baseline",
            bloom_dist_ok,
            (
                f"found {len(bloom_levels)} bloom level(s); "
                f"understand/apply present={has_higher}"
            ),
        )
    )

    difficulties = {q.difficulty for q in worksheet.questions}
    diff_ok = len(difficulties) >= 2
    checks.append(
        _check(
            "difficulty_distribution_baseline",
            diff_ok,
            f"found {len(difficulties)} difficulty band(s); required at least 2",
        )
    )

    text_blob = _collect_text(worksheet)
    claim = find_unsupported_alignment_claim(text_blob)
    alignment_ok = claim is None
    checks.append(
        _check(
            "unsupported_alignment_claim_absent",
            alignment_ok,
            "no unsupported curriculum/board alignment claims detected"
            if alignment_ok
            else f"unsupported alignment claim detected near {claim!r}",
        )
    )

    status = (
        EducationalQualityStatus.PASS
        if all(check.passed for check in checks)
        else EducationalQualityStatus.FAIL
    )
    return EducationalQualityResult(status=status, checks=tuple(checks))


def educational_quality_from_summary(
    summary: Mapping[str, object] | None,
) -> EducationalQualityResult | None:
    if summary is None:
        return None
    status_raw = summary.get("status")
    checks_raw = summary.get("checks")
    if not isinstance(status_raw, str) or not isinstance(checks_raw, Sequence):
        return None
    try:
        status = EducationalQualityStatus(status_raw)
    except ValueError:
        return None
    checks: list[EducationalQualityCheck] = []
    for item in checks_raw:
        if not isinstance(item, Mapping):
            return None
        code = item.get("code")
        passed = item.get("passed")
        explanation = item.get("explanation")
        if (
            not isinstance(code, str)
            or not isinstance(passed, bool)
            or not isinstance(explanation, str)
        ):
            return None
        checks.append(EducationalQualityCheck(code=code, passed=passed, explanation=explanation))
    return EducationalQualityResult(status=status, checks=tuple(checks))

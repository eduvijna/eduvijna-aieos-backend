"""TOS-DEV03 Educational Quality Baseline unit tests."""

from __future__ import annotations

import pytest

from aieos.platform.education.quality_baseline import (
    EducationalQualityStatus,
    evaluate_educational_quality_baseline_v1,
)
from tests.domains.teaching.worksheet_fixtures import valid_worksheet_payload

pytestmark = pytest.mark.tos_dev03


def test_quality_baseline_passes_valid_worksheet() -> None:
    result = evaluate_educational_quality_baseline_v1(valid_worksheet_payload())
    assert result.status is EducationalQualityStatus.PASS
    assert all(check.passed for check in result.checks)


def test_quality_baseline_fails_alignment_claim() -> None:
    result = evaluate_educational_quality_baseline_v1(
        valid_worksheet_payload(include_alignment_claim=True)
    )
    assert result.status is EducationalQualityStatus.FAIL
    codes = {c.code: c.passed for c in result.checks}
    assert codes["unsupported_alignment_claim_absent"] is False


def test_quality_baseline_fails_single_bloom() -> None:
    result = evaluate_educational_quality_baseline_v1(
        valid_worksheet_payload(single_bloom=True)
    )
    assert result.status is EducationalQualityStatus.FAIL
    codes = {c.code: c.passed for c in result.checks}
    assert codes["bloom_distribution_baseline"] is False


def test_quality_baseline_fails_question_count() -> None:
    payload = valid_worksheet_payload(question_count=6)
    payload["questions"] = payload["questions"][:3]
    result = evaluate_educational_quality_baseline_v1(payload)
    assert result.status is EducationalQualityStatus.FAIL
    codes = {c.code: c.passed for c in result.checks}
    assert codes["schema_valid"] is False or codes["question_count_valid"] is False

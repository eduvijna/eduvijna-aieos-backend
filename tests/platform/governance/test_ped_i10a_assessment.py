"""PED-I10A AssetUseAssessment contract tests."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from aieos.platform.resources.asset_use import (
    AssetUseAssessment,
    AssetUseRejectionReason,
    InvalidAssetUseAssessmentError,
)

pytestmark = pytest.mark.ped_i10a


class TestAssetUseAssessment:
    def test_usable_true_without_reason(self) -> None:
        assessment = AssetUseAssessment(usable=True)
        assert assessment.usable is True
        assert assessment.reason_code is None

    def test_usable_true_with_reason_invalid(self) -> None:
        with pytest.raises(InvalidAssetUseAssessmentError):
            AssetUseAssessment(
                usable=True, reason_code=AssetUseRejectionReason.NOT_FOUND
            )

    def test_usable_false_requires_closed_reason(self) -> None:
        for reason in AssetUseRejectionReason:
            assessment = AssetUseAssessment(usable=False, reason_code=reason)
            assert assessment.usable is False
            assert assessment.reason_code is reason

    def test_usable_false_without_reason_invalid(self) -> None:
        with pytest.raises(InvalidAssetUseAssessmentError):
            AssetUseAssessment(usable=False)

    def test_unknown_reason_cannot_be_created(self) -> None:
        with pytest.raises(ValueError):
            AssetUseRejectionReason("TOTALLY_UNKNOWN")
        with pytest.raises(InvalidAssetUseAssessmentError):
            AssetUseAssessment(usable=False, reason_code="NOT_FOUND")  # type: ignore[arg-type]

    def test_optional_authority_revision(self) -> None:
        ok = AssetUseAssessment(usable=True, authority_revision=0)
        assert ok.authority_revision == 0
        with pytest.raises(InvalidAssetUseAssessmentError):
            AssetUseAssessment(usable=True, authority_revision=-1)
        with pytest.raises(InvalidAssetUseAssessmentError):
            AssetUseAssessment(usable=True, authority_revision=True)  # type: ignore[arg-type]

    def test_optional_observed_at_timezone(self) -> None:
        ok = AssetUseAssessment(
            usable=True, observed_at=datetime(2026, 1, 1, tzinfo=UTC)
        )
        assert ok.observed_at is not None
        with pytest.raises(InvalidAssetUseAssessmentError):
            AssetUseAssessment(
                usable=True, observed_at=datetime(2026, 1, 1)
            )

    def test_frozen(self) -> None:
        assessment = AssetUseAssessment(usable=True)
        with pytest.raises(Exception):
            assessment.usable = False  # type: ignore[misc]
        _ = uuid4()

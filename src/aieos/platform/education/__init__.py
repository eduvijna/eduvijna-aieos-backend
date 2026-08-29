"""Shared educational intelligence contracts."""

from aieos.platform.education.quality_baseline import (
    EducationalQualityCheck,
    EducationalQualityResult,
    EducationalQualityStatus,
    educational_quality_from_summary,
    evaluate_educational_quality_baseline_v1,
    find_unsupported_alignment_claim,
)

__all__ = [
    "EducationalQualityCheck",
    "EducationalQualityResult",
    "EducationalQualityStatus",
    "educational_quality_from_summary",
    "evaluate_educational_quality_baseline_v1",
    "find_unsupported_alignment_claim",
]

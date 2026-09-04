"""Teaching-owned immutable provenance vocabulary for remediation origin.

Mirrors the Assessment class_result_level closed set for snapshot purposes only.
This enum is Teaching-owned provenance vocabulary — not Assessment ownership,
not Mastery, and not a recommendation.
"""

from __future__ import annotations

from enum import StrEnum

from aieos.domains.teaching.domain.errors import InvalidRemediationOriginError


class ClassResultLevelSnapshot(StrEnum):
    DEMONSTRATED = "DEMONSTRATED"
    MIXED = "MIXED"
    NOT_YET_DEMONSTRATED = "NOT_YET_DEMONSTRATED"


def parse_class_result_level_snapshot(
    value: ClassResultLevelSnapshot | str,
) -> ClassResultLevelSnapshot:
    if isinstance(value, ClassResultLevelSnapshot):
        return value
    if not isinstance(value, str):
        raise InvalidRemediationOriginError(
            "source_class_result_level_snapshot must be a string"
        )
    try:
        return ClassResultLevelSnapshot(value)
    except ValueError as exc:
        raise InvalidRemediationOriginError(
            "source_class_result_level_snapshot must be one of "
            "DEMONSTRATED, MIXED, NOT_YET_DEMONSTRATED"
        ) from exc

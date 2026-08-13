"""Frozen Generic Content stewardship vocabulary.

Generating belongs to workflow/operation truth.
Published belongs to publication truth.
Reject is a ReviewDecision, not a stewardship state.
Archive is not purge.
"""

from __future__ import annotations

from enum import StrEnum

from aieos.domains.content.domain.errors import InvalidStewardshipStateError


class StewardshipState(StrEnum):
    DRAFT = "DRAFT"
    GENERATED = "GENERATED"
    IN_REVIEW = "IN_REVIEW"
    APPROVED = "APPROVED"
    ARCHIVED = "ARCHIVED"


FROZEN_STEWARDSHIP_STATES: frozenset[StewardshipState] = frozenset(StewardshipState)


def parse_stewardship_state(value: str | StewardshipState) -> StewardshipState:
    if isinstance(value, StewardshipState):
        return value
    try:
        return StewardshipState(value)
    except ValueError as exc:
        raise InvalidStewardshipStateError(
            f"unknown stewardship state {value!r}; "
            f"allowed={sorted(s.value for s in StewardshipState)}"
        ) from exc

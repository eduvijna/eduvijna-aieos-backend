"""Teaching Intent types.

A Teaching Intent is the request shape that enters Work creation. It is not a
durable aggregate and has no System of Record table. Only the resulting
TeachingWork is durable; intent_type records which intent produced the Work.

The enum is intentionally extensible: adding a member here must be paired with
a migration that widens the teaching.works intent_type CHECK constraint.
"""

from __future__ import annotations

from enum import StrEnum

from aieos.domains.teaching.domain.errors import InvalidIntentTypeError


class IntentType(StrEnum):
    PREPARE_TOMORROW = "prepare_tomorrow"


def parse_intent_type(value: IntentType | str) -> IntentType:
    if isinstance(value, IntentType):
        return value
    if not isinstance(value, str):
        raise InvalidIntentTypeError("intent_type must be a string")
    try:
        return IntentType(value)
    except ValueError as exc:
        raise InvalidIntentTypeError(
            f"intent_type is not a registered Teaching intent type: {value!r}"
        ) from exc

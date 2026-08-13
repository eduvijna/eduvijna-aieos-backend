"""Frozen Generic Content origin vocabulary.

Origin describes provenance category. It grants no approval, publication
authority, or trust.
"""

from __future__ import annotations

from enum import StrEnum

from aieos.domains.content.domain.errors import InvalidOriginError


class ContentOrigin(StrEnum):
    HUMAN = "HUMAN"
    AI = "AI"
    IMPORT = "IMPORT"
    SYSTEM = "SYSTEM"


FROZEN_CONTENT_ORIGINS: frozenset[ContentOrigin] = frozenset(ContentOrigin)


def parse_content_origin(value: str | ContentOrigin) -> ContentOrigin:
    if isinstance(value, ContentOrigin):
        return value
    try:
        return ContentOrigin(value)
    except ValueError as exc:
        raise InvalidOriginError(
            f"unknown content origin {value!r}; "
            f"allowed={sorted(o.value for o in ContentOrigin)}"
        ) from exc

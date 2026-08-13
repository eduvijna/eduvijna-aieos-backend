"""Strong AIEOS revision If-Match parser."""

from __future__ import annotations

import re

from aieos.platform.api.http_errors import InvalidIfMatchError, PreconditionRequiredError

_STRONG_REVISION = re.compile(r'^"r(0|[1-9][0-9]*)"$')


def parse_if_match(raw: str | None) -> int:
    if raw is None or not str(raw).strip():
        raise PreconditionRequiredError()
    value = str(raw).strip()
    if value == "*" or value.startswith("W/") or "," in value:
        raise InvalidIfMatchError()
    matched = _STRONG_REVISION.fullmatch(value)
    if matched is None:
        raise InvalidIfMatchError()
    return int(matched.group(1))

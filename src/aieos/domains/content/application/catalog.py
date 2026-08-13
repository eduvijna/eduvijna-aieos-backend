"""In-memory content-type catalog. Not a database registry."""

from __future__ import annotations

from collections.abc import Iterable


class StaticContentTypeCatalog:
    """Application-level registered content types for this process."""

    def __init__(self, type_names: Iterable[str]) -> None:
        self._names = frozenset(name.strip() for name in type_names if name.strip())

    def contains(self, content_type: str) -> bool:
        return content_type.strip() in self._names

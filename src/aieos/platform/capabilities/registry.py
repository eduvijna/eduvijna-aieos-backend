"""In-memory code-controlled Capability Registry."""

from __future__ import annotations

from aieos.platform.capabilities.models import (
    CAPABILITY_EDUCATION_GENERATE_WORKSHEET,
    GENERATE_WORKSHEET_V1,
    CapabilityDescriptor,
)


class CapabilityNotFoundError(LookupError):
    """Raised when a capability_id is not registered."""


class DuplicateCapabilityError(ValueError):
    """Raised when registering a capability_id that already exists."""


class CapabilityRegistry:
    """Code-controlled registry. Not a dynamic marketplace."""

    def __init__(self) -> None:
        self._by_id: dict[str, CapabilityDescriptor] = {}

    def register(self, descriptor: CapabilityDescriptor) -> None:
        if descriptor.capability_id in self._by_id:
            raise DuplicateCapabilityError(
                f"capability {descriptor.capability_id!r} is already registered"
            )
        self._by_id[descriptor.capability_id] = descriptor

    def get(self, capability_id: str) -> CapabilityDescriptor:
        try:
            return self._by_id[capability_id]
        except KeyError as exc:
            raise CapabilityNotFoundError(
                f"capability {capability_id!r} is not registered"
            ) from exc

    def list(self) -> tuple[CapabilityDescriptor, ...]:
        return tuple(self._by_id[key] for key in sorted(self._by_id))


def build_default_capability_registry() -> CapabilityRegistry:
    registry = CapabilityRegistry()
    registry.register(GENERATE_WORKSHEET_V1)
    return registry


__all__ = [
    "CAPABILITY_EDUCATION_GENERATE_WORKSHEET",
    "CapabilityNotFoundError",
    "CapabilityRegistry",
    "DuplicateCapabilityError",
    "build_default_capability_registry",
]

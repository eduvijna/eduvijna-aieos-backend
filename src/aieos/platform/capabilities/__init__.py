"""AIEOS Capability Registry (code-controlled, not marketplace)."""

from aieos.platform.capabilities.models import (
    CAPABILITY_EDUCATION_GENERATE_WORKSHEET,
    GENERATE_WORKSHEET_V1,
    CapabilityContract,
    CapabilityDescriptor,
    SideEffectClassification,
)
from aieos.platform.capabilities.registry import (
    CapabilityNotFoundError,
    CapabilityRegistry,
    DuplicateCapabilityError,
    build_default_capability_registry,
)

__all__ = [
    "CAPABILITY_EDUCATION_GENERATE_WORKSHEET",
    "GENERATE_WORKSHEET_V1",
    "CapabilityContract",
    "CapabilityDescriptor",
    "CapabilityNotFoundError",
    "CapabilityRegistry",
    "DuplicateCapabilityError",
    "SideEffectClassification",
    "build_default_capability_registry",
]

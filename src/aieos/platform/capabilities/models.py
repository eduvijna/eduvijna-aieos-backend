"""Code-controlled AIEOS Capability Registry contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class SideEffectClassification(StrEnum):
    NONE = "none"
    CREATES_REVIEWABLE_CONTENT = "creates_reviewable_content"
    MUTATES_STATE = "mutates_state"
    EXTERNAL_IO = "external_io"


@dataclass(frozen=True, slots=True)
class CapabilityContract:
    """Stable input/output contract identifiers for a registered capability."""

    contract_id: str
    version: int

    def __post_init__(self) -> None:
        if not isinstance(self.contract_id, str) or not self.contract_id.strip():
            raise ValueError("contract_id must be a non-empty string")
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 1:
            raise ValueError("contract version must be a positive integer")
        object.__setattr__(self, "contract_id", self.contract_id.strip())

    def as_ref(self) -> str:
        return f"{self.contract_id}@{self.version}"


@dataclass(frozen=True, slots=True)
class CapabilityDescriptor:
    """Provider-independent capability metadata. Not a marketplace entry."""

    capability_id: str
    name: str
    description: str
    version: str
    input_contract: CapabilityContract
    output_contract: CapabilityContract
    permission_requirement: str
    side_effect_classification: SideEffectClassification
    human_approval_required: bool
    timeout_seconds: int
    mcp_exposed: bool

    def __post_init__(self) -> None:
        if not isinstance(self.capability_id, str) or not self.capability_id.strip():
            raise ValueError("capability_id must be a non-empty string")
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("name must be a non-empty string")
        if not isinstance(self.description, str) or not self.description.strip():
            raise ValueError("description must be a non-empty string")
        if not isinstance(self.version, str) or not self.version.strip():
            raise ValueError("version must be a non-empty string")
        if not isinstance(self.permission_requirement, str) or not self.permission_requirement.strip():
            raise ValueError("permission_requirement must be a non-empty string")
        if isinstance(self.timeout_seconds, bool) or not isinstance(self.timeout_seconds, int):
            raise ValueError("timeout_seconds must be a positive integer")
        if self.timeout_seconds < 1:
            raise ValueError("timeout_seconds must be a positive integer")
        if not isinstance(self.human_approval_required, bool):
            raise ValueError("human_approval_required must be a bool")
        if not isinstance(self.mcp_exposed, bool):
            raise ValueError("mcp_exposed must be a bool")
        if not isinstance(self.side_effect_classification, SideEffectClassification):
            raise ValueError("side_effect_classification must be a SideEffectClassification")
        object.__setattr__(self, "capability_id", self.capability_id.strip())
        object.__setattr__(self, "name", self.name.strip())
        object.__setattr__(self, "description", self.description.strip())
        object.__setattr__(self, "version", self.version.strip())
        object.__setattr__(
            self, "permission_requirement", self.permission_requirement.strip()
        )


CAPABILITY_EDUCATION_GENERATE_WORKSHEET = "education.generate_worksheet"

GENERATE_WORKSHEET_V1 = CapabilityDescriptor(
    capability_id=CAPABILITY_EDUCATION_GENERATE_WORKSHEET,
    name="Generate Worksheet",
    description=(
        "Generate a structured teacher-review worksheet draft from Teaching Work context."
    ),
    version="1.0.0",
    input_contract=CapabilityContract("education.worksheet_generation_input", 1),
    output_contract=CapabilityContract("education.worksheet", 1),
    permission_requirement="education.generate_worksheet",
    side_effect_classification=SideEffectClassification.CREATES_REVIEWABLE_CONTENT,
    human_approval_required=True,
    timeout_seconds=90,
    mcp_exposed=False,
)

"""Provider-neutral structured model gateway contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class StructuredGenerationRequest[T: BaseModel]:
    capability_id: str
    instructions: str
    input_text: str
    output_type: type[T]
    max_output_tokens: int


@dataclass(frozen=True, slots=True)
class StructuredGenerationResult[T: BaseModel]:
    provider_id: str
    model_id: str
    provider_response_id: str | None
    parsed_output: T
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None


class ModelGatewayError(Exception):
    """Base error for Model Gateway failures. Never carries secrets or raw bodies."""


class ModelProviderUnavailable(ModelGatewayError):
    """Provider/config/network unavailable."""


class ModelGenerationFailed(ModelGatewayError):
    """Provider returned a failure or incomplete generation."""


class ModelOutputInvalid(ModelGatewayError):
    """Provider output could not be parsed into the requested schema."""


class StructuredModelGateway(Protocol):
    """Domain-facing port. Implementations may import provider SDKs; callers must not."""

    def generate_structured[T: BaseModel](
        self, request: StructuredGenerationRequest[T]
    ) -> StructuredGenerationResult[T]: ...

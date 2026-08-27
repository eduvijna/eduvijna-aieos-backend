"""Deterministic fake Model Gateway for tests. Never calls a provider."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from aieos.platform.ai.gateway import (
    ModelGenerationFailed,
    ModelGatewayError,
    ModelOutputInvalid,
    ModelProviderUnavailable,
    StructuredGenerationRequest,
    StructuredGenerationResult,
)


@dataclass
class FakeStructuredModelGateway:
    """Injectable fake. Records calls; never logs prompts or responses."""

    result_factory: Callable[[StructuredGenerationRequest[Any]], Any] | None = None
    error: ModelGatewayError | None = None
    provider_id: str = "fake"
    model_id: str = "fake-model"
    provider_response_id: str = "fake-response-1"
    input_tokens: int | None = 10
    output_tokens: int | None = 20
    total_tokens: int | None = 30
    calls: list[StructuredGenerationRequest[Any]] = field(default_factory=list)

    def generate_structured[T: BaseModel](
        self, request: StructuredGenerationRequest[T]
    ) -> StructuredGenerationResult[T]:
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        if self.result_factory is not None:
            parsed = self.result_factory(request)
        else:
            raise ModelGenerationFailed("fake gateway has no result_factory configured")
        if not isinstance(parsed, request.output_type):
            raise ModelOutputInvalid("fake result_factory returned unexpected type")
        return StructuredGenerationResult(
            provider_id=self.provider_id,
            model_id=self.model_id,
            provider_response_id=self.provider_response_id,
            parsed_output=parsed,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            total_tokens=self.total_tokens,
        )

    def fail_unavailable(self, message: str = "provider unavailable") -> None:
        self.error = ModelProviderUnavailable(message)

    def fail_generation(self, message: str = "generation failed") -> None:
        self.error = ModelGenerationFailed(message)

    def fail_invalid(self, message: str = "output invalid") -> None:
        self.error = ModelOutputInvalid(message)

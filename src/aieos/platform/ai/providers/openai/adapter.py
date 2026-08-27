"""OpenAI Responses API adapter. Only this package may import openai."""

from __future__ import annotations

from typing import Any

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI
from pydantic import BaseModel, ValidationError

from aieos.platform.ai.config import OpenAIProviderConfig
from aieos.platform.ai.gateway import (
    ModelGenerationFailed,
    ModelOutputInvalid,
    ModelProviderUnavailable,
    StructuredGenerationRequest,
    StructuredGenerationResult,
)


class OpenAIStructuredModelGateway:
    """Structured generation via client.responses.parse. store=False, max_retries=0."""

    def __init__(self, config: OpenAIProviderConfig, *, client: Any | None = None) -> None:
        self._config = config
        self._client = client or OpenAI(
            api_key=config.api_key,
            timeout=config.timeout_seconds,
            max_retries=0,
        )

    def generate_structured[T: BaseModel](
        self, request: StructuredGenerationRequest[T]
    ) -> StructuredGenerationResult[T]:
        max_tokens = min(request.max_output_tokens, self._config.max_output_tokens)
        try:
            response = self._client.responses.parse(
                model=self._config.model_id,
                instructions=request.instructions,
                input=request.input_text,
                text_format=request.output_type,
                max_output_tokens=max_tokens,
                store=False,
                tools=[],
            )
        except (APIConnectionError, APITimeoutError) as exc:
            raise ModelProviderUnavailable("OpenAI provider unavailable") from exc
        except APIStatusError as exc:
            if exc.status_code in {401, 403, 404, 429, 500, 502, 503, 504}:
                raise ModelProviderUnavailable("OpenAI provider unavailable") from exc
            raise ModelGenerationFailed("OpenAI generation failed") from exc
        except Exception as exc:  # noqa: BLE001 — normalize any SDK failure
            raise ModelGenerationFailed("OpenAI generation failed") from exc

        parsed = getattr(response, "output_parsed", None)
        if parsed is None:
            raise ModelOutputInvalid("OpenAI response missing structured output")
        if not isinstance(parsed, request.output_type):
            try:
                parsed = request.output_type.model_validate(parsed)
            except (ValidationError, TypeError, ValueError) as exc:
                raise ModelOutputInvalid("OpenAI structured output invalid") from exc

        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "input_tokens", None) if usage is not None else None
        output_tokens = getattr(usage, "output_tokens", None) if usage is not None else None
        total_tokens = getattr(usage, "total_tokens", None) if usage is not None else None
        response_id = getattr(response, "id", None)
        model_id = getattr(response, "model", None) or self._config.model_id

        return StructuredGenerationResult(
            provider_id=self._config.provider_id,
            model_id=str(model_id),
            provider_response_id=None if response_id is None else str(response_id),
            parsed_output=parsed,
            input_tokens=int(input_tokens) if isinstance(input_tokens, int) else None,
            output_tokens=int(output_tokens) if isinstance(output_tokens, int) else None,
            total_tokens=int(total_tokens) if isinstance(total_tokens, int) else None,
        )

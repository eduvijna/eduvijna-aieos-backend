"""OpenAI Responses API adapter. Only this package may import openai."""

from __future__ import annotations

import logging
from typing import Any, Mapping

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI
from pydantic import BaseModel, ValidationError

from aieos.platform.ai.config import OpenAIProviderConfig
from aieos.platform.ai.gateway import (
    ModelAdapterContractFailed,
    ModelGenerationFailed,
    ModelOutputIncomplete,
    ModelOutputInvalid,
    ModelOutputMissing,
    ModelProviderUnavailable,
    ModelRequestRejected,
    StructuredGenerationRequest,
    StructuredGenerationResult,
)

_LOGGER = logging.getLogger(__name__)

_UNAVAILABLE_STATUS_CODES = frozenset({401, 403, 404, 429, 500, 502, 503, 504})
_BOUNDED_IDENTIFIER_MAX_LEN = 64


def _safe_scalar(value: object) -> str | int | float | bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        if isinstance(value, str) and len(value) > 128:
            return None
        return value
    return None


def _bounded_identifier(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    if len(value) > _BOUNDED_IDENTIFIER_MAX_LEN:
        return None
    if all(ch.isalnum() or ch in "_-" for ch in value):
        return value
    return None


def _extract_provider_error_scalars(body: object) -> dict[str, str | int | float | bool]:
    """Allowlisted scalar extractor. Never returns nested bodies or messages."""
    out: dict[str, str | int | float | bool] = {}
    if not isinstance(body, Mapping):
        return out
    error = body.get("error")
    if not isinstance(error, Mapping):
        return out
    for key in ("type", "code"):
        scalar = _safe_scalar(error.get(key))
        if scalar is not None:
            out[f"provider_error_{key}"] = scalar
    return out


def _usage_tokens(response: object) -> dict[str, int]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return {}
    out: dict[str, int] = {}
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        value = getattr(usage, key, None)
        if isinstance(value, int):
            out[key] = value
    output_details = getattr(usage, "output_tokens_details", None)
    if output_details is not None:
        reasoning = getattr(output_details, "reasoning_tokens", None)
        if isinstance(reasoning, int):
            out["reasoning_tokens"] = reasoning
    return out


def _inspect_response_metadata(response: object) -> dict[str, object]:
    """Extract safe post-return metadata only. Never returns generated text."""
    metadata: dict[str, object] = {}
    response_status = _bounded_identifier(getattr(response, "status", None))
    if response_status is not None:
        metadata["response_status"] = response_status
    incomplete = getattr(response, "incomplete_details", None)
    if incomplete is not None:
        reason = _bounded_identifier(getattr(incomplete, "reason", None))
        if reason is not None:
            metadata["incomplete_reason"] = reason
    response_id = getattr(response, "id", None)
    if isinstance(response_id, str) and len(response_id) <= 128:
        metadata["provider_response_id"] = response_id
    model_id = getattr(response, "model", None)
    bounded_model = _bounded_identifier(model_id)
    if bounded_model is not None:
        metadata["model_id"] = bounded_model
    metadata.update(_usage_tokens(response))

    output_item_types: list[str] = []
    content_item_types: list[str] = []
    refusal_present = False
    output = getattr(response, "output", None)
    if isinstance(output, list):
        for item in output:
            item_type = _bounded_identifier(getattr(item, "type", None))
            if item_type is not None and item_type not in output_item_types:
                output_item_types.append(item_type)
            content = getattr(item, "content", None)
            if isinstance(content, list):
                message_status = _bounded_identifier(getattr(item, "status", None))
                if message_status is not None:
                    metadata.setdefault("output_message_status", message_status)
                for part in content:
                    part_type = _bounded_identifier(getattr(part, "type", None))
                    if part_type is not None:
                        if part_type not in content_item_types:
                            content_item_types.append(part_type)
                        if part_type == "refusal":
                            refusal_present = True
    if output_item_types:
        metadata["output_item_types"] = tuple(output_item_types)
    if content_item_types:
        metadata["content_item_types"] = tuple(content_item_types)
    metadata["refusal_present"] = refusal_present
    return metadata


def _log_ai_diagnostic(*, classification: str, **fields: object) -> None:
    payload: dict[str, object] = {
        "provider": "openai",
        "operation": "responses.parse",
        "classification": classification,
    }
    for key, value in fields.items():
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            if isinstance(value, str) and len(value) > 128:
                continue
            payload[key] = value
        elif isinstance(value, tuple) and all(isinstance(item, str) for item in value):
            payload[key] = value
    _LOGGER.warning("openai_structured_generation_failed", extra={"aieos_ai": payload})


def _log_failure(
    *,
    classification: str,
    exception_class: str,
    http_status: int | None = None,
    provider_error_type: str | int | float | bool | None = None,
    provider_error_code: str | int | float | bool | None = None,
    provider_request_id: str | None = None,
) -> None:
    _log_ai_diagnostic(
        classification=classification,
        exception_class=exception_class,
        http_status=http_status,
        provider_error_type=provider_error_type,
        provider_error_code=provider_error_code,
        provider_request_id=provider_request_id,
    )


def _classify_missing_output(metadata: dict[str, object]) -> None:
    """Raise and log when parse succeeded but structured output is absent."""
    if metadata.get("refusal_present") is True:
        _log_ai_diagnostic(classification="model_output_missing", **metadata)
        raise ModelOutputMissing("OpenAI structured output missing")
    _log_ai_diagnostic(classification="model_output_missing", **metadata)
    raise ModelOutputMissing("OpenAI structured output missing")


def _classify_incomplete_output(metadata: dict[str, object]) -> None:
    _log_ai_diagnostic(classification="model_output_incomplete", **metadata)
    raise ModelOutputIncomplete("OpenAI structured output incomplete")


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
                reasoning={"effort": "none"},
            )
        except (APIConnectionError, APITimeoutError) as exc:
            _log_failure(
                classification="model_provider_unavailable",
                exception_class=type(exc).__name__,
            )
            raise ModelProviderUnavailable("OpenAI provider unavailable") from exc
        except APIStatusError as exc:
            status = int(exc.status_code)
            scalars = _extract_provider_error_scalars(getattr(exc, "body", None))
            request_id = getattr(exc, "request_id", None)
            if not isinstance(request_id, str):
                request_id = None
            if status in _UNAVAILABLE_STATUS_CODES:
                _log_failure(
                    classification="model_provider_unavailable",
                    exception_class=type(exc).__name__,
                    http_status=status,
                    provider_error_type=scalars.get("provider_error_type"),
                    provider_error_code=scalars.get("provider_error_code"),
                    provider_request_id=request_id,
                )
                raise ModelProviderUnavailable("OpenAI provider unavailable") from exc
            if 400 <= status < 500:
                _log_failure(
                    classification="model_request_rejected",
                    exception_class=type(exc).__name__,
                    http_status=status,
                    provider_error_type=scalars.get("provider_error_type"),
                    provider_error_code=scalars.get("provider_error_code"),
                    provider_request_id=request_id,
                )
                raise ModelRequestRejected("OpenAI request rejected") from exc
            _log_failure(
                classification="model_generation_failed",
                exception_class=type(exc).__name__,
                http_status=status,
                provider_error_type=scalars.get("provider_error_type"),
                provider_error_code=scalars.get("provider_error_code"),
                provider_request_id=request_id,
            )
            raise ModelGenerationFailed("OpenAI generation failed") from exc
        except ValidationError as exc:
            _log_failure(
                classification="model_output_invalid",
                exception_class=type(exc).__name__,
            )
            raise ModelOutputInvalid("OpenAI structured output invalid") from exc
        except (TypeError, ValueError) as exc:
            _log_failure(
                classification="model_adapter_contract_failed",
                exception_class=type(exc).__name__,
            )
            raise ModelAdapterContractFailed(
                "OpenAI adapter contract failed"
            ) from exc
        except Exception as exc:  # noqa: BLE001 — residual SDK failure
            _log_failure(
                classification="model_generation_failed",
                exception_class=type(exc).__name__,
            )
            raise ModelGenerationFailed("OpenAI generation failed") from exc

        metadata = _inspect_response_metadata(response)
        parsed = getattr(response, "output_parsed", None)
        if parsed is None:
            if metadata.get("response_status") == "incomplete":
                _classify_incomplete_output(metadata)
            _classify_missing_output(metadata)
        if not isinstance(parsed, request.output_type):
            try:
                parsed = request.output_type.model_validate(parsed)
            except (ValidationError, TypeError, ValueError) as exc:
                _log_ai_diagnostic(
                    classification="model_output_invalid",
                    exception_class=type(exc).__name__,
                    **metadata,
                )
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

"""TOS-DEV03R3 offline OpenAI adapter failure taxonomy and sanitized diagnostics.

No network. No API key. Mocked client only.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
from openai import APIConnectionError, APIStatusError, APITimeoutError
from pydantic import ValidationError

from aieos.domains.education.worksheet_v1 import WorksheetV1
from aieos.platform.ai.config import OpenAIProviderConfig
from aieos.platform.ai.gateway import (
    ModelAdapterContractFailed,
    ModelGenerationFailed,
    ModelOutputInvalid,
    ModelProviderUnavailable,
    ModelRequestRejected,
    StructuredGenerationRequest,
)
from aieos.platform.ai.providers.openai.adapter import (
    OpenAIStructuredModelGateway,
    _extract_provider_error_scalars,
)
from tests.domains.teaching.worksheet_fixtures import valid_worksheet_model

pytestmark = [pytest.mark.tos_dev03, pytest.mark.tos_dev03r3]

_PROHIBITED_LOG_SUBSTRINGS = (
    "sk-",
    "Authorization",
    "Bearer",
    "api_key",
    "AIEOS_OPENAI_API_KEY",
    "instructions",
    "input_text",
    "error message",
)


def _config() -> OpenAIProviderConfig:
    return OpenAIProviderConfig(
        provider_id="openai",
        model_id="gpt-5.6-terra",
        api_key="offline-dummy-key",
        max_output_tokens=4000,
        timeout_seconds=75.0,
    )


def _request() -> StructuredGenerationRequest[WorksheetV1]:
    return StructuredGenerationRequest(
        capability_id="education.generate_worksheet",
        instructions="offline-test-instructions-must-never-appear-in-logs",
        input_text="offline-test-input-must-never-appear-in-logs",
        output_type=WorksheetV1,
        max_output_tokens=4000,
    )


def _api_status(code: int, *, body: dict[str, object] | None = None) -> APIStatusError:
    req = httpx.Request("POST", "https://api.openai.com/v1/responses")
    payload = body if body is not None else {"error": {"type": "invalid_request", "code": "bad"}}
    resp = httpx.Response(code, request=req, json=payload)
    exc = APIStatusError("error", response=resp, body=payload)
    return exc


def _gateway(mock_client: Any) -> OpenAIStructuredModelGateway:
    return OpenAIStructuredModelGateway(_config(), client=mock_client)


class TestOpenAIAdapterTaxonomy:
    def test_valid_worksheet_success_unchanged(self) -> None:
        valid = valid_worksheet_model()
        client = MagicMock()
        client.responses.parse.return_value = SimpleNamespace(
            output_parsed=valid,
            usage=SimpleNamespace(input_tokens=1, output_tokens=2, total_tokens=3),
            id="resp_ok",
            model="gpt-5.6-terra",
        )
        result = _gateway(client).generate_structured(_request())
        assert result.provider_id == "openai"
        assert result.model_id == "gpt-5.6-terra"
        assert isinstance(result.parsed_output, WorksheetV1)

    @pytest.mark.parametrize("status", [400, 409, 422])
    def test_http_4xx_request_rejected(self, status: int) -> None:
        client = MagicMock()
        client.responses.parse.side_effect = _api_status(status)
        with pytest.raises(ModelRequestRejected):
            _gateway(client).generate_structured(_request())

    @pytest.mark.parametrize("status", [401, 403, 404, 429, 500, 502, 503, 504])
    def test_http_unavailable_family(self, status: int) -> None:
        client = MagicMock()
        client.responses.parse.side_effect = _api_status(status)
        with pytest.raises(ModelProviderUnavailable):
            _gateway(client).generate_structured(_request())

    def test_connection_and_timeout_unavailable(self) -> None:
        client = MagicMock()
        client.responses.parse.side_effect = APIConnectionError(
            request=httpx.Request("POST", "https://api.openai.com/v1/responses")
        )
        with pytest.raises(ModelProviderUnavailable):
            _gateway(client).generate_structured(_request())
        client.responses.parse.side_effect = APITimeoutError(
            request=httpx.Request("POST", "https://api.openai.com/v1/responses")
        )
        with pytest.raises(ModelProviderUnavailable):
            _gateway(client).generate_structured(_request())

    def test_validation_error_inside_parse_is_output_invalid(self) -> None:
        client = MagicMock()
        client.responses.parse.side_effect = ValidationError.from_exception_data(
            "WorksheetV1",
            [{"type": "missing", "loc": ("title",), "input": {}, "msg": "Field required"}],
        )
        with pytest.raises(ModelOutputInvalid):
            _gateway(client).generate_structured(_request())

    def test_type_error_is_adapter_contract_failed(self) -> None:
        client = MagicMock()
        client.responses.parse.side_effect = TypeError("unexpected keyword")
        with pytest.raises(ModelAdapterContractFailed):
            _gateway(client).generate_structured(_request())

    def test_value_error_is_adapter_contract_failed(self) -> None:
        client = MagicMock()
        client.responses.parse.side_effect = ValueError("bad argument")
        with pytest.raises(ModelAdapterContractFailed):
            _gateway(client).generate_structured(_request())

    def test_residual_exception_is_generation_failed(self) -> None:
        client = MagicMock()
        client.responses.parse.side_effect = RuntimeError("unexpected sdk failure")
        with pytest.raises(ModelGenerationFailed):
            _gateway(client).generate_structured(_request())


class TestSanitizedDiagnostics:
    def test_extractor_allowlists_only_type_and_code(self) -> None:
        body = {
            "error": {
                "type": "invalid_request_error",
                "code": "invalid_value",
                "message": "SECRET_MESSAGE_MUST_NOT_EXTRACT",
                "param": "model",
            }
        }
        scalars = _extract_provider_error_scalars(body)
        assert scalars == {
            "provider_error_type": "invalid_request_error",
            "provider_error_code": "invalid_value",
        }
        assert "message" not in scalars
        assert "SECRET" not in str(scalars)

    def test_failure_log_contains_no_prohibited_content(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = MagicMock()
        body = {
            "error": {
                "type": "invalid_request_error",
                "code": "bad_request",
                "message": "sk-secret-key-value Authorization Bearer leak",
            }
        }
        client.responses.parse.side_effect = _api_status(400, body=body)
        captured: list[tuple[str, dict[str, object]]] = []

        def _warning(msg: object, *args: object, **kwargs: object) -> None:
            extra = kwargs.get("extra")
            payload = extra.get("aieos_ai", {}) if isinstance(extra, dict) else {}
            captured.append((str(msg), dict(payload) if isinstance(payload, dict) else {}))

        monkeypatch.setattr(
            "aieos.platform.ai.providers.openai.adapter._LOGGER.warning",
            _warning,
        )
        with pytest.raises(ModelRequestRejected):
            _gateway(client).generate_structured(_request())
        assert len(captured) == 1
        message, aieos = captured[0]
        assert message == "openai_structured_generation_failed"
        assert aieos["provider"] == "openai"
        assert aieos["operation"] == "responses.parse"
        assert aieos["classification"] == "model_request_rejected"
        assert aieos["http_status"] == 400
        assert aieos["exception_class"] == "APIStatusError"
        assert aieos["provider_error_type"] == "invalid_request_error"
        assert aieos["provider_error_code"] == "bad_request"
        blob = f"{message} {aieos}"
        for needle in _PROHIBITED_LOG_SUBSTRINGS:
            assert needle not in blob
        assert "offline-test-instructions" not in blob
        assert "offline-test-input" not in blob
        assert "SECRET" not in blob
        assert "sk-secret" not in blob

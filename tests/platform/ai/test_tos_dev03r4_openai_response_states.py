"""TOS-DEV03R4 offline OpenAI post-return response state matrix."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from aieos.domains.education.worksheet_v1 import WorksheetV1
from aieos.platform.ai.config import OpenAIProviderConfig
from aieos.platform.ai.gateway import (
    ModelOutputIncomplete,
    ModelOutputInvalid,
    ModelOutputMissing,
    StructuredGenerationRequest,
)
from aieos.platform.ai.providers.openai.adapter import OpenAIStructuredModelGateway
from tests.domains.teaching.worksheet_fixtures import valid_worksheet_model

pytestmark = [pytest.mark.tos_dev03, pytest.mark.tos_dev03r4]

_PROHIBITED_LOG_SUBSTRINGS = (
    "sk-",
    "Authorization",
    "Bearer",
    "api_key",
    "instructions",
    "input_text",
    "SECRET",
    "refusal text",
    "should not log",
    "offline-test-instructions",
    "offline-test-input",
)


def _config() -> OpenAIProviderConfig:
    return OpenAIProviderConfig(
        provider_id="openai",
        model_id="gpt-5.6-terra",
        api_key="offline-dummy-key",
        max_output_tokens=8000,
        timeout_seconds=75.0,
    )


def _request() -> StructuredGenerationRequest[WorksheetV1]:
    return StructuredGenerationRequest(
        capability_id="education.generate_worksheet",
        instructions="offline-test-instructions-must-never-appear-in-logs",
        input_text="offline-test-input-must-never-appear-in-logs",
        output_type=WorksheetV1,
        max_output_tokens=8000,
    )


def _gateway(mock_client: Any) -> OpenAIStructuredModelGateway:
    return OpenAIStructuredModelGateway(_config(), client=mock_client)


def _capture_logs(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    captured: list[dict[str, object]] = []

    def _warning(msg: object, *args: object, **kwargs: object) -> None:
        extra = kwargs.get("extra")
        payload = extra.get("aieos_ai", {}) if isinstance(extra, dict) else {}
        captured.append(dict(payload) if isinstance(payload, dict) else {})

    monkeypatch.setattr(
        "aieos.platform.ai.providers.openai.adapter._LOGGER.warning",
        _warning,
    )
    return captured


class TestPostReturnResponseStates:
    def test_incomplete_max_output_tokens(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = MagicMock()
        client.responses.parse.return_value = SimpleNamespace(
            status="incomplete",
            incomplete_details=SimpleNamespace(reason="max_output_tokens"),
            output_parsed=None,
            output=(),
            usage=SimpleNamespace(
                input_tokens=100,
                output_tokens=8000,
                total_tokens=8100,
                output_tokens_details=SimpleNamespace(reasoning_tokens=0),
            ),
            id="resp_incomplete",
            model="gpt-5.6-terra",
        )
        logs = _capture_logs(monkeypatch)
        with pytest.raises(ModelOutputIncomplete):
            _gateway(client).generate_structured(_request())
        assert len(logs) == 1
        diag = logs[0]
        assert diag["classification"] == "model_output_incomplete"
        assert diag["response_status"] == "incomplete"
        assert diag["incomplete_reason"] == "max_output_tokens"
        assert diag["provider_response_id"] == "resp_incomplete"
        blob = str(diag)
        for needle in _PROHIBITED_LOG_SUBSTRINGS:
            assert needle not in blob

    def test_completed_without_parsed_output(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = MagicMock()
        client.responses.parse.return_value = SimpleNamespace(
            status="completed",
            incomplete_details=None,
            output_parsed=None,
            output=[
                SimpleNamespace(
                    type="message",
                    status="completed",
                    content=[SimpleNamespace(type="output_text", text="should not log")],
                )
            ],
            usage=SimpleNamespace(
                input_tokens=50,
                output_tokens=10,
                total_tokens=60,
                output_tokens_details=SimpleNamespace(reasoning_tokens=0),
            ),
            id="resp_missing",
            model="gpt-5.6-terra",
        )
        logs = _capture_logs(monkeypatch)
        with pytest.raises(ModelOutputMissing):
            _gateway(client).generate_structured(_request())
        assert logs[0]["classification"] == "model_output_missing"
        assert logs[0]["response_status"] == "completed"
        assert logs[0]["refusal_present"] is False
        assert "should not log" not in str(logs[0])

    def test_explicit_refusal_classified_as_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = MagicMock()
        client.responses.parse.return_value = SimpleNamespace(
            status="completed",
            incomplete_details=None,
            output_parsed=None,
            output=[
                SimpleNamespace(
                    type="message",
                    status="completed",
                    content=[
                        SimpleNamespace(
                            type="refusal",
                            refusal="SECRET REFUSAL TEXT must never appear in logs",
                        )
                    ],
                )
            ],
            usage=None,
            id="resp_refusal",
            model="gpt-5.6-terra",
        )
        logs = _capture_logs(monkeypatch)
        with pytest.raises(ModelOutputMissing):
            _gateway(client).generate_structured(_request())
        assert logs[0]["classification"] == "model_output_missing"
        assert logs[0]["refusal_present"] is True
        assert logs[0]["content_item_types"] == ("refusal",)
        blob = str(logs[0])
        assert "SECRET" not in blob
        assert "refusal text" not in blob.lower()

    def test_validation_error_inside_parse_still_output_invalid(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = MagicMock()
        client.responses.parse.side_effect = ValidationError.from_exception_data(
            "WorksheetV1",
            [{"type": "missing", "loc": ("title",), "input": {}, "msg": "Field required"}],
        )
        logs = _capture_logs(monkeypatch)
        with pytest.raises(ModelOutputInvalid):
            _gateway(client).generate_structured(_request())
        assert logs[0]["classification"] == "model_output_invalid"

    def test_valid_worksheet_success_unchanged(self) -> None:
        valid = valid_worksheet_model()
        client = MagicMock()
        client.responses.parse.return_value = SimpleNamespace(
            output_parsed=valid,
            status="completed",
            usage=SimpleNamespace(
                input_tokens=1,
                output_tokens=2,
                total_tokens=3,
                output_tokens_details=SimpleNamespace(reasoning_tokens=0),
            ),
            id="resp_ok",
            model="gpt-5.6-terra",
            output=(),
            incomplete_details=None,
        )
        result = _gateway(client).generate_structured(_request())
        assert result.provider_response_id == "resp_ok"
        assert result.input_tokens == 1
        assert isinstance(result.parsed_output, WorksheetV1)

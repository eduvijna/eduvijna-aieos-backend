"""TOS-DEV03R4 mocked OpenAI request shape for worksheet generation."""

from __future__ import annotations

import uuid
from datetime import date
from unittest.mock import MagicMock

import pytest

from aieos.domains.education.application.generate_worksheet import (
    WORKSHEET_MAX_OUTPUT_TOKENS,
    GenerateWorksheetCapability,
)
from aieos.domains.education.application.models import WorksheetGenerationInput
from aieos.domains.education.worksheet_v1 import WorksheetV1
from aieos.platform.ai.config import OpenAIProviderConfig
from aieos.platform.ai.providers.openai.adapter import OpenAIStructuredModelGateway
from aieos.platform.resources import ResourceRef
from tests.domains.teaching.worksheet_fixtures import valid_worksheet_model

pytestmark = [pytest.mark.tos_dev03, pytest.mark.tos_dev03r4]


def test_responses_parse_kwargs_for_worksheet() -> None:
    client = MagicMock()
    valid = valid_worksheet_model()
    client.responses.parse.return_value = MagicMock(
        output_parsed=valid,
        status="completed",
        usage=MagicMock(
            input_tokens=10,
            output_tokens=20,
            total_tokens=30,
            output_tokens_details=MagicMock(reasoning_tokens=0),
        ),
        id="resp_req",
        model="gpt-5.6-terra",
        output=[],
        incomplete_details=None,
    )
    config = OpenAIProviderConfig(
        provider_id="openai",
        model_id="gpt-5.6-terra",
        api_key="offline-dummy-key",
        max_output_tokens=8000,
        timeout_seconds=75.0,
    )
    gateway = OpenAIStructuredModelGateway(config, client=client)
    capability = GenerateWorksheetCapability(gateway)
    capability.execute(
        WorksheetGenerationInput(
            goal_text="Grade 5 fractions worksheet",
            target_date=date(2026, 8, 29),
            locale="en-IN",
            class_label="Grade 5",
            subject="Mathematics",
            topic="Fractions",
            work_ref=ResourceRef("teaching.work", uuid.uuid7(), 1),
        )
    )
    assert client.responses.parse.call_count == 1
    kwargs = client.responses.parse.call_args.kwargs
    assert kwargs["model"] == "gpt-5.6-terra"
    assert kwargs["store"] is False
    assert kwargs["tools"] == []
    assert kwargs["max_output_tokens"] == WORKSHEET_MAX_OUTPUT_TOKENS == 8000
    assert kwargs["reasoning"] == {"effort": "none"}
    assert kwargs["text_format"] is WorksheetV1


def test_openai_client_max_retries_zero() -> None:
    config = OpenAIProviderConfig(
        provider_id="openai",
        model_id="gpt-5.6-terra",
        api_key="offline-dummy-key",
        max_output_tokens=8000,
        timeout_seconds=75.0,
    )
    gateway = OpenAIStructuredModelGateway(config)
    assert gateway._client.max_retries == 0

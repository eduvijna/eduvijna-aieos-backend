"""TOS-DEV03R4 architecture guards for output completion reliability."""

from __future__ import annotations

import inspect

import pytest

from aieos.domains.education.application.generate_worksheet import (
    WORKSHEET_MAX_OUTPUT_TOKENS,
    GenerateWorksheetCapability,
)
from aieos.platform.ai.config import DEFAULT_MAX_OUTPUT_TOKENS
from aieos.platform.ai.gateway import (
    ModelOutputIncomplete,
    ModelOutputMissing,
)
from aieos.platform.ai.providers.openai.adapter import OpenAIStructuredModelGateway
from tests.dbutil import REPO_ROOT

pytestmark = [pytest.mark.tos_dev03, pytest.mark.tos_dev03r4]

GATEWAY = REPO_ROOT / "src" / "aieos" / "platform" / "ai" / "gateway.py"


def test_provider_neutral_gateway_has_output_completion_types() -> None:
    source = GATEWAY.read_text(encoding="utf-8")
    assert "class ModelOutputIncomplete" in source
    assert "class ModelOutputMissing" in source
    assert "openai" not in source.lower()


def test_worksheet_capability_default_token_budget() -> None:
    sig = inspect.signature(GenerateWorksheetCapability.__init__)
    default = sig.parameters["max_output_tokens"].default
    assert default == WORKSHEET_MAX_OUTPUT_TOKENS == 8000


def test_development_config_ceiling_supports_worksheet_budget() -> None:
    assert DEFAULT_MAX_OUTPUT_TOKENS >= WORKSHEET_MAX_OUTPUT_TOKENS


def test_adapter_passes_reasoning_effort_none() -> None:
    source = (REPO_ROOT / "src" / "aieos" / "platform" / "ai" / "providers" / "openai" / "adapter.py").read_text(
        encoding="utf-8"
    )
    assert 'reasoning={"effort": "none"}' in source


def test_refusal_classified_under_model_output_missing_not_separate_type() -> None:
    adapter = REPO_ROOT / "src" / "aieos" / "platform" / "ai" / "providers" / "openai" / "adapter.py"
    source = adapter.read_text(encoding="utf-8")
    assert "ModelOutputRefused" not in source
    assert "refusal_present" in source
    assert "ModelOutputMissing" in source

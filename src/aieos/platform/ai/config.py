"""Environment-backed AI provider configuration. Secrets stay in env only."""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_AI_PROVIDER = "openai"
DEFAULT_AI_MODEL = "gpt-5.6-terra"
DEFAULT_MAX_OUTPUT_TOKENS = 8000
DEFAULT_TIMEOUT_SECONDS = 75.0

ENV_AI_PROVIDER = "AIEOS_AI_PROVIDER"
ENV_AI_MODEL = "AIEOS_AI_MODEL"
ENV_OPENAI_API_KEY = "AIEOS_OPENAI_API_KEY"
ENV_AI_MAX_OUTPUT_TOKENS = "AIEOS_AI_MAX_OUTPUT_TOKENS"
ENV_AI_TIMEOUT_SECONDS = "AIEOS_AI_TIMEOUT_SECONDS"
ENV_GENERATION_LEASE_SECONDS = "AIEOS_GENERATION_LEASE_SECONDS"

DEFAULT_GENERATION_LEASE_SECONDS = 120


@dataclass(frozen=True, slots=True)
class OpenAIProviderConfig:
    provider_id: str
    model_id: str
    api_key: str
    max_output_tokens: int
    timeout_seconds: float

    def __post_init__(self) -> None:
        if not self.api_key:
            raise ValueError("OpenAI API key is required for the OpenAI adapter")
        if self.max_output_tokens < 1:
            raise ValueError("max_output_tokens must be positive")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")


def load_openai_provider_config_from_env(
    environ: dict[str, str] | None = None,
) -> OpenAIProviderConfig:
    """Load OpenAI adapter config. Never logs or returns the key for persistence."""
    env = os.environ if environ is None else environ
    provider = (env.get(ENV_AI_PROVIDER) or DEFAULT_AI_PROVIDER).strip()
    if provider != "openai":
        raise ValueError(f"unsupported AIEOS_AI_PROVIDER={provider!r}; expected openai")
    model = (env.get(ENV_AI_MODEL) or DEFAULT_AI_MODEL).strip()
    if not model:
        raise ValueError("AIEOS_AI_MODEL must be a non-empty model identifier")
    api_key = (env.get(ENV_OPENAI_API_KEY) or "").strip()
    if not api_key:
        raise ValueError("AIEOS_OPENAI_API_KEY is not set")
    raw_tokens = (env.get(ENV_AI_MAX_OUTPUT_TOKENS) or str(DEFAULT_MAX_OUTPUT_TOKENS)).strip()
    try:
        max_tokens = int(raw_tokens)
    except ValueError as exc:
        raise ValueError("AIEOS_AI_MAX_OUTPUT_TOKENS must be an integer") from exc
    raw_timeout = (env.get(ENV_AI_TIMEOUT_SECONDS) or str(DEFAULT_TIMEOUT_SECONDS)).strip()
    try:
        timeout = float(raw_timeout)
    except ValueError as exc:
        raise ValueError("AIEOS_AI_TIMEOUT_SECONDS must be a number") from exc
    return OpenAIProviderConfig(
        provider_id="openai",
        model_id=model,
        api_key=api_key,
        max_output_tokens=max_tokens,
        timeout_seconds=timeout,
    )


def load_generation_lease_seconds(
    environ: dict[str, str] | None = None,
) -> int:
    """Lease duration for RUNNING GenerationRun recovery (NON_PRODUCTION default)."""
    env = os.environ if environ is None else environ
    raw = (
        env.get(ENV_GENERATION_LEASE_SECONDS) or str(DEFAULT_GENERATION_LEASE_SECONDS)
    ).strip()
    try:
        seconds = int(raw)
    except ValueError as exc:
        raise ValueError("AIEOS_GENERATION_LEASE_SECONDS must be an integer") from exc
    if seconds < 1:
        raise ValueError("AIEOS_GENERATION_LEASE_SECONDS must be positive")
    return seconds

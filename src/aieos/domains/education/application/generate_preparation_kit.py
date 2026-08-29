"""Native education.generate_preparation_kit capability. Provider-neutral."""

from __future__ import annotations

from aieos.domains.education.application.models import (
    PreparationKitGenerationDraft,
    PreparationKitGenerationInput,
    ProviderMetadata,
)
from aieos.domains.education.application.preparation_artifacts import (
    build_preparation_artifact_payloads,
)
from aieos.domains.education.application.preparation_prompt import (
    INSTRUCTIONS,
    build_preparation_input_text,
)
from aieos.domains.education.preparation_kit_v1 import PreparationKitV1
from aieos.platform.ai.gateway import (
    StructuredGenerationRequest,
    StructuredModelGateway,
)
from aieos.platform.capabilities.models import (
    CAPABILITY_EDUCATION_GENERATE_PREPARATION_KIT,
)

# Implementation default only — not product/architecture authority. Overridable.
PREPARATION_KIT_MAX_OUTPUT_TOKENS = 16000


class GeneratePreparationKitCapability:
    """One structured gateway call, then deterministic final-payload build.

    Does not persist Content, orchestrate generation runs, or evaluate Educational Quality.
    """

    def __init__(
        self,
        model_gateway: StructuredModelGateway,
        *,
        max_output_tokens: int = PREPARATION_KIT_MAX_OUTPUT_TOKENS,
    ) -> None:
        if isinstance(max_output_tokens, bool) or not isinstance(max_output_tokens, int):
            raise ValueError("max_output_tokens must be a positive integer")
        if max_output_tokens < 1:
            raise ValueError("max_output_tokens must be a positive integer")
        self._model_gateway = model_gateway
        self._max_output_tokens = max_output_tokens

    def execute(
        self, generation_input: PreparationKitGenerationInput
    ) -> PreparationKitGenerationDraft:
        result = self._model_gateway.generate_structured(
            StructuredGenerationRequest(
                capability_id=CAPABILITY_EDUCATION_GENERATE_PREPARATION_KIT,
                instructions=INSTRUCTIONS,
                input_text=build_preparation_input_text(generation_input),
                output_type=PreparationKitV1,
                max_output_tokens=self._max_output_tokens,
            )
        )
        artifacts = build_preparation_artifact_payloads(result.parsed_output)
        return PreparationKitGenerationDraft(
            preparation_kit=result.parsed_output,
            artifacts=artifacts,
            provider_metadata=ProviderMetadata(
                provider_id=result.provider_id,
                model_id=result.model_id,
                provider_response_id=result.provider_response_id,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                total_tokens=result.total_tokens,
            ),
        )

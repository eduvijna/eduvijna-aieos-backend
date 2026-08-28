"""Native education.generate_worksheet capability. Provider-neutral."""

from __future__ import annotations

from aieos.domains.education.application.models import (
    ProviderMetadata,
    WorksheetGenerationDraft,
    WorksheetGenerationInput,
)
from aieos.domains.education.application.prompt import (
    INSTRUCTIONS,
    build_worksheet_input_text,
)
from aieos.domains.education.worksheet_v1 import WorksheetV1
from aieos.platform.capabilities.models import CAPABILITY_EDUCATION_GENERATE_WORKSHEET
from aieos.platform.education.quality_baseline import (
    EducationalQualityStatus,
    evaluate_educational_quality_baseline_v1,
)
from aieos.platform.ai.gateway import (
    StructuredGenerationRequest,
    StructuredModelGateway,
)

WORKSHEET_MAX_OUTPUT_TOKENS = 8000


class EducationalQualityFailed(Exception):
    """Raised when Educational Quality Baseline V1 fails after model generation."""

    def __init__(self, draft_without_pass: WorksheetGenerationDraft) -> None:
        super().__init__("educational quality baseline failed")
        self.draft = draft_without_pass


class GenerateWorksheetCapability:
    """Calls Model Gateway then Educational Quality Baseline before Content."""

    def __init__(
        self,
        model_gateway: StructuredModelGateway,
        *,
        max_output_tokens: int = WORKSHEET_MAX_OUTPUT_TOKENS,
    ) -> None:
        self._model_gateway = model_gateway
        self._max_output_tokens = max_output_tokens

    def execute(self, generation_input: WorksheetGenerationInput) -> WorksheetGenerationDraft:
        result = self._model_gateway.generate_structured(
            StructuredGenerationRequest(
                capability_id=CAPABILITY_EDUCATION_GENERATE_WORKSHEET,
                instructions=INSTRUCTIONS,
                input_text=build_worksheet_input_text(generation_input),
                output_type=WorksheetV1,
                max_output_tokens=self._max_output_tokens,
            )
        )
        quality = evaluate_educational_quality_baseline_v1(result.parsed_output)
        draft = WorksheetGenerationDraft(
            worksheet_payload=result.parsed_output,
            provider_metadata=ProviderMetadata(
                provider_id=result.provider_id,
                model_id=result.model_id,
                provider_response_id=result.provider_response_id,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                total_tokens=result.total_tokens,
            ),
            educational_quality_result=quality,
        )
        if quality.status is not EducationalQualityStatus.PASS:
            raise EducationalQualityFailed(draft)
        return draft

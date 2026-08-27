"""AI platform package: Model Gateway + GenerationRun SoR."""

from aieos.platform.ai.gateway import (
    ModelGenerationFailed,
    ModelGatewayError,
    ModelOutputInvalid,
    ModelProviderUnavailable,
    StructuredGenerationRequest,
    StructuredGenerationResult,
    StructuredModelGateway,
)

__all__ = [
    "ModelGenerationFailed",
    "ModelGatewayError",
    "ModelOutputInvalid",
    "ModelProviderUnavailable",
    "StructuredGenerationRequest",
    "StructuredGenerationResult",
    "StructuredModelGateway",
]

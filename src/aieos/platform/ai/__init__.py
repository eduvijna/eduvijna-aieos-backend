"""AI platform package: Model Gateway + GenerationRun SoR."""

from aieos.platform.ai.gateway import (
    ModelGenerationFailed,
    ModelGatewayError,
    ModelAdapterContractFailed,
    ModelOutputInvalid,
    ModelProviderUnavailable,
    ModelRequestRejected,
    StructuredGenerationRequest,
    StructuredGenerationResult,
    StructuredModelGateway,
)

__all__ = [
    "ModelAdapterContractFailed",
    "ModelGenerationFailed",
    "ModelGatewayError",
    "ModelOutputInvalid",
    "ModelProviderUnavailable",
    "ModelRequestRejected",
    "StructuredGenerationRequest",
    "StructuredGenerationResult",
    "StructuredModelGateway",
]

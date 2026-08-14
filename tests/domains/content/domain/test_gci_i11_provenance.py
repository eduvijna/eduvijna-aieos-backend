"""GCI-I11 typed AIGenerationProvenanceV1 domain contract."""

from __future__ import annotations

import uuid

import pytest

from aieos.domains.content.domain.errors import InvalidAIGenerationProvenanceError
from aieos.domains.content.domain.provenance import (
    AIGenerationProvenanceV1,
    ai_generation_provenance_as_json,
    ai_generation_provenance_from_json,
)
from aieos.platform.resources import ResourceRef

pytestmark = pytest.mark.gci_i11


def _valid(**overrides) -> AIGenerationProvenanceV1:
    base = dict(
        generation_run_ref=ResourceRef("generation.run", uuid.uuid7(), 1),
        prompt_execution_ref=None,
        provider_id="test.provider",
        model_id="neutral-model",
        capability_id="content.generate.lesson",
        source_refs=(),
        policy_refs=(),
        evaluation_refs=(),
        correlation_id=uuid.uuid7(),
    )
    base.update(overrides)
    return AIGenerationProvenanceV1(**base)


class TestAIGenerationProvenanceV1:
    def test_round_trip_preserves_equality(self) -> None:
        original = _valid(
            prompt_execution_ref=ResourceRef("prompt.execution", uuid.uuid7(), None),
            source_refs=(ResourceRef("source.doc", uuid.uuid7(), 0),),
            policy_refs=(ResourceRef("policy.rule", uuid.uuid7(), None),),
            evaluation_refs=(ResourceRef("eval.report", uuid.uuid7(), 2),),
        )
        restored = ai_generation_provenance_from_json(
            ai_generation_provenance_as_json(original)
        )
        assert restored == original
        assert restored.kind == "ai_generation"
        assert restored.schema_version == 1

    @pytest.mark.parametrize(
        "mutate",
        [
            lambda d: d.pop("generation_run_ref"),
            lambda d: d.__setitem__("extra", "nope"),
            lambda d: d.__setitem__("kind", "other"),
            lambda d: d.__setitem__("schema_version", 2),
            lambda d: d.__setitem__("schema_version", True),
            lambda d: d.__setitem__("schema_version", 1.0),
            lambda d: d.__setitem__("schema_version", "1"),
            lambda d: d.__setitem__("generation_run_ref", {"bad": True}),
            lambda d: d.__setitem__(
                "prompt_execution_ref",
                {"resource_type": "x", "resource_id": "not-a-uuid", "resource_revision": None},
            ),
            lambda d: d.__setitem__("provider_id", "BadProvider"),
            lambda d: d.__setitem__("model_id", ""),
            lambda d: d.__setitem__("capability_id", ""),
            lambda d: d.__setitem__("correlation_id", "not-uuid"),
            lambda d: d.__setitem__("source_refs", "nope"),
            lambda d: d.__setitem__(
                "source_refs",
                [{"resource_type": "!!", "resource_id": str(uuid.uuid7()), "resource_revision": None}],
            ),
        ],
    )
    def test_parser_rejects_invalid_shapes(self, mutate) -> None:
        payload = ai_generation_provenance_as_json(_valid())
        mutate(payload)
        with pytest.raises(InvalidAIGenerationProvenanceError):
            ai_generation_provenance_from_json(payload)

    @pytest.mark.parametrize(
        "secret_key",
        [
            "api_key",
            "access_token",
            "authorization_header",
            "provider_credentials",
            "client_secret",
        ],
    )
    def test_secret_fields_rejected(self, secret_key: str) -> None:
        payload = ai_generation_provenance_as_json(_valid())
        payload[secret_key] = "SECRET"
        with pytest.raises(InvalidAIGenerationProvenanceError):
            ai_generation_provenance_from_json(payload)

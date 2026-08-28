"""GCI-I11 typed AIGenerationProvenanceV1/V2 domain contract."""

from __future__ import annotations

import ast
import uuid

import pytest

from aieos.domains.content.domain.errors import InvalidAIGenerationProvenanceError
from aieos.domains.content.domain.provenance import (
    AIGenerationProvenanceV1,
    AIGenerationProvenanceV2,
    ai_generation_provenance_as_json,
    ai_generation_provenance_from_json,
)
from aieos.platform.resources import ResourceRef
from tests.dbutil import REPO_ROOT

pytestmark = pytest.mark.gci_i11

_PROVENANCE_PATH = REPO_ROOT / "src" / "aieos" / "domains" / "content" / "domain" / "provenance.py"
_FORBIDDEN_IMPORTS = (
    "openai",
    "temporalio",
    "langchain",
    "llama_index",
    "crewai",
    "autogen",
    "semantic_kernel",
)


def _valid_v1(**overrides) -> AIGenerationProvenanceV1:
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


def _valid_v2(**overrides) -> AIGenerationProvenanceV2:
    base = dict(
        generation_run_ref=ResourceRef("ai.generation_run", uuid.uuid7(), 1),
        prompt_execution_ref=None,
        provider_id="test.provider",
        model_id="neutral-model",
        capability_id="education.generate_preparation_kit",
        source_refs=(ResourceRef("teaching.work", uuid.uuid7(), 2),),
        policy_refs=(),
        evaluation_refs=(),
        correlation_id=uuid.uuid7(),
        artifact_kind="worksheet",
    )
    base.update(overrides)
    return AIGenerationProvenanceV2(**base)


class TestAIGenerationProvenanceV1:
    def test_round_trip_preserves_equality(self) -> None:
        original = _valid_v1(
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
            lambda d: d.__setitem__("artifact_kind", "worksheet"),
            lambda d: d.__setitem__("schema_version", True),
            lambda d: d.__setitem__("schema_version", 1.0),
            lambda d: d.__setitem__("schema_version", "1"),
            lambda d: d.__setitem__("schema_version", 3),
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
        payload = ai_generation_provenance_as_json(_valid_v1())
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
            "prompt",
            "raw_prompt",
            "response",
            "raw_response",
            "authorization",
            "chain_of_thought",
            "reasoning",
        ],
    )
    def test_secret_fields_rejected(self, secret_key: str) -> None:
        payload = ai_generation_provenance_as_json(_valid_v1())
        payload[secret_key] = "SECRET"
        with pytest.raises(InvalidAIGenerationProvenanceError):
            ai_generation_provenance_from_json(payload)


class TestAIGenerationProvenanceV2:
    @pytest.mark.parametrize(
        "artifact_kind",
        [
            "lesson_plan",
            "worksheet",
            "quiz",
            "homework",
            "answer_key",
            "teacher_notes",
        ],
    )
    def test_round_trip_accepts_dev04_artifact_kinds(self, artifact_kind: str) -> None:
        original = _valid_v2(artifact_kind=artifact_kind)
        restored = ai_generation_provenance_from_json(
            ai_generation_provenance_as_json(original)
        )
        assert restored == original
        assert isinstance(restored, AIGenerationProvenanceV2)
        assert restored.schema_version == 2
        assert restored.artifact_kind == artifact_kind

    def test_exact_v2_key_set(self) -> None:
        payload = ai_generation_provenance_as_json(_valid_v2())
        assert set(payload.keys()) == {
            "kind",
            "schema_version",
            "generation_run_ref",
            "prompt_execution_ref",
            "provider_id",
            "model_id",
            "capability_id",
            "source_refs",
            "policy_refs",
            "evaluation_refs",
            "correlation_id",
            "artifact_kind",
        }
        assert "artifact_kind" not in ai_generation_provenance_as_json(_valid_v1())

    def test_v1_json_not_interpreted_as_v2(self) -> None:
        v1 = ai_generation_provenance_from_json(ai_generation_provenance_as_json(_valid_v1()))
        assert isinstance(v1, AIGenerationProvenanceV1)

    def test_v2_json_not_interpreted_as_v1(self) -> None:
        v2 = ai_generation_provenance_from_json(ai_generation_provenance_as_json(_valid_v2()))
        assert isinstance(v2, AIGenerationProvenanceV2)

    @pytest.mark.parametrize(
        "mutate",
        [
            lambda d: d.pop("artifact_kind"),
            lambda d: d.__setitem__("artifact_kind", ""),
            lambda d: d.__setitem__("artifact_kind", "BadKind"),
            lambda d: d.__setitem__("extra", "nope"),
            lambda d: d.__setitem__("schema_version", 1),
        ],
    )
    def test_v2_parser_rejects_invalid_shapes(self, mutate) -> None:
        payload = ai_generation_provenance_as_json(_valid_v2())
        mutate(payload)
        with pytest.raises(InvalidAIGenerationProvenanceError):
            ai_generation_provenance_from_json(payload)

    @pytest.mark.parametrize(
        "secret_key",
        [
            "prompt",
            "raw_prompt",
            "response",
            "raw_response",
            "api_key",
            "authorization",
            "chain_of_thought",
            "reasoning",
        ],
    )
    def test_v2_secret_fields_rejected(self, secret_key: str) -> None:
        payload = ai_generation_provenance_as_json(_valid_v2())
        payload[secret_key] = "SECRET"
        with pytest.raises(InvalidAIGenerationProvenanceError):
            ai_generation_provenance_from_json(payload)


def test_provenance_module_has_no_forbidden_provider_imports() -> None:
    tree = ast.parse(_PROVENANCE_PATH.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])
    for forbidden in _FORBIDDEN_IMPORTS:
        assert forbidden not in imports

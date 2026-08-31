"""TOS-DEV04-I04 GeneratePreparationKitCapability and architecture guards."""

from __future__ import annotations

import ast
import inspect
from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest

from aieos.domains.content.application import ai_preparation_for_review as i03_mod
from aieos.domains.education.application.generate_preparation_kit import (
    PREPARATION_KIT_MAX_OUTPUT_TOKENS,
    GeneratePreparationKitCapability,
)
from aieos.domains.education.application.models import PreparationKitGenerationInput
from aieos.domains.education.application.preparation_artifacts import (
    PreparationArtifactBuildFailed,
)
from aieos.domains.education.application.preparation_prompt import (
    INSTRUCTIONS,
    build_preparation_input_text,
)
from aieos.domains.education.preparation_kit_v1 import PreparationKitV1
from aieos.platform.ai.fake import FakeStructuredModelGateway
from aieos.platform.ai.gateway import ModelGenerationFailed, ModelProviderUnavailable
from aieos.platform.capabilities.models import (
    CAPABILITY_EDUCATION_GENERATE_PREPARATION_KIT,
)
from aieos.platform.capabilities.registry import build_default_capability_registry
from aieos.platform.resources import ResourceRef
from tests.dbutil import REPO_ROOT
from tests.domains.education.test_tos_dev04_i04_preparation_artifacts import (
    valid_kit,
    valid_kit_payload,
)

pytestmark = pytest.mark.tos_dev04_i04

_EDUCATION_APP = REPO_ROOT / "src" / "aieos" / "domains" / "education" / "application"
_FORBIDDEN_IMPORTS = (
    "openai",
    "anthropic",
    "temporalio",
    "langchain",
    "llama_index",
    "crewai",
    "autogen",
    "semantic_kernel",
    "mcp",
)


def _work_ref(*, revision: int | None = 1) -> ResourceRef:
    return ResourceRef("teaching.work", uuid4(), revision)


def _valid_input(**overrides: object) -> PreparationKitGenerationInput:
    base = {
        "work_ref": _work_ref(),
        "goal_text": "Prepare a coherent photosynthesis lesson kit",
        "class_label": "Grade 6",
        "subject": "Science",
        "topic": "Photosynthesis",
        "target_date": date(2026, 9, 1),
        "locale": "en-IN",
    }
    base.update(overrides)
    return PreparationKitGenerationInput(**base)  # type: ignore[arg-type]


def _module_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])
    return imports


class TestPreparationKitGenerationInput:
    def test_valid_accepted(self) -> None:
        inp = _valid_input()
        assert inp.work_ref.resource_type == "teaching.work"
        assert inp.work_ref.resource_revision == 1

    def test_blank_goal_rejected(self) -> None:
        with pytest.raises(ValueError, match="goal_text"):
            _valid_input(goal_text="   ")

    def test_blank_locale_rejected(self) -> None:
        with pytest.raises(ValueError, match="locale"):
            _valid_input(locale="")

    def test_non_teaching_work_rejected(self) -> None:
        with pytest.raises(ValueError, match="teaching.work"):
            _valid_input(work_ref=ResourceRef("content.item", uuid4(), 1))

    def test_null_revision_rejected(self) -> None:
        with pytest.raises(ValueError, match="resource_revision"):
            _valid_input(work_ref=_work_ref(revision=None))

    @pytest.mark.parametrize("field", ["class_label", "subject", "topic"])
    def test_whitespace_optional_rejected(self, field: str) -> None:
        with pytest.raises(ValueError, match=field):
            _valid_input(**{field: "  "})

    def test_invalid_input_does_not_call_gateway(self) -> None:
        gateway = FakeStructuredModelGateway(
            result_factory=lambda _req: valid_kit(),
        )
        capability = GeneratePreparationKitCapability(gateway)
        with pytest.raises(ValueError):
            PreparationKitGenerationInput(
                work_ref=_work_ref(revision=None),
                goal_text="goal",
                class_label=None,
                subject=None,
                topic=None,
                target_date=date(2026, 9, 1),
                locale="en",
            )
        assert gateway.call_count == 0
        # Capability never reached
        assert capability._max_output_tokens == PREPARATION_KIT_MAX_OUTPUT_TOKENS


class TestPreparationPrompt:
    def test_safety_and_authority_rules(self) -> None:
        text = INSTRUCTIONS.lower()
        assert "teacher review" in text
        assert "answer_key" in text and (
            "do not generate answer_key" in text or "absent from model output" in text
        )
        assert "shared_learning_objectives are canonical" in text
        assert "6–12" in INSTRUCTIONS or "6-12" in INSTRUCTIONS
        assert "cbse" in text and "do not claim" in text
        assert "pii" in text or "personal student data" in text
        assert "chain-of-thought" in text or "chain of thought" in text
        assert "do not approve" in text
        assert "do not publish" in text
        for banned in ("self-approve", "mark as approved", "generate answer_key"):
            # Explicit positive: must not instruct generation of answer_key
            pass
        assert "generate answer_key" not in text.replace("do not generate answer_key", "")

    def test_input_text_contains_work_id_revision_and_goal(self) -> None:
        work_id = uuid4()
        inp = _valid_input(
            work_ref=ResourceRef("teaching.work", work_id, 7),
            goal_text="Exact educational goal text",
        )
        text = build_preparation_input_text(inp)
        assert str(work_id) in text
        assert "@r7" in text
        assert "Exact educational goal text" in text
        assert "teaching.work" in text


class TestGeneratePreparationKitCapability:
    def test_one_gateway_call_and_six_artifacts(self) -> None:
        kit = valid_kit()
        work_id = uuid4()
        revision = 42
        gateway = FakeStructuredModelGateway(
            result_factory=lambda _req: kit,
            provider_id="fake-provider",
            model_id="fake-model-x",
            provider_response_id="resp-99",
            input_tokens=11,
            output_tokens=22,
            total_tokens=33,
        )
        capability = GeneratePreparationKitCapability(gateway, max_output_tokens=12345)
        draft = capability.execute(
            _valid_input(
                work_ref=ResourceRef("teaching.work", work_id, revision),
                goal_text="Exact goal for capability test",
            )
        )

        assert gateway.call_count == 1
        request = gateway.calls[0]
        assert request.capability_id == CAPABILITY_EDUCATION_GENERATE_PREPARATION_KIT
        assert request.capability_id == "education.generate_preparation_kit"
        assert request.output_type is PreparationKitV1
        assert request.max_output_tokens == 12345
        assert str(work_id) in request.input_text
        assert f"@r{revision}" in request.input_text
        assert "Exact goal for capability test" in request.input_text

        assert draft.preparation_kit is kit
        assert draft.provider_metadata.provider_id == "fake-provider"
        assert draft.provider_metadata.model_id == "fake-model-x"
        assert draft.provider_metadata.provider_response_id == "resp-99"
        assert draft.provider_metadata.input_tokens == 11
        assert draft.provider_metadata.output_tokens == 22
        assert draft.provider_metadata.total_tokens == 33

        assert draft.artifacts.lesson_plan is not None
        assert draft.artifacts.worksheet is not None
        assert draft.artifacts.quiz is not None
        assert draft.artifacts.homework is not None
        assert draft.artifacts.answer_key is not None
        assert draft.artifacts.teacher_notes is not None

    def test_default_max_output_tokens_is_bounded_positive(self) -> None:
        assert PREPARATION_KIT_MAX_OUTPUT_TOKENS == 16000
        assert PREPARATION_KIT_MAX_OUTPUT_TOKENS > 0
        sig = inspect.signature(GeneratePreparationKitCapability.__init__)
        assert sig.parameters["max_output_tokens"].default == PREPARATION_KIT_MAX_OUTPUT_TOKENS

    def test_build_failure_does_not_retry_gateway(self) -> None:
        kit = valid_kit(worksheet_question_count=3)
        gateway = FakeStructuredModelGateway(result_factory=lambda _req: kit)
        capability = GeneratePreparationKitCapability(gateway)
        with pytest.raises(PreparationArtifactBuildFailed):
            capability.execute(_valid_input())
        assert gateway.call_count == 1

    def test_gateway_errors_propagate(self) -> None:
        gateway = FakeStructuredModelGateway(error=ModelProviderUnavailable("down"))
        capability = GeneratePreparationKitCapability(gateway)
        with pytest.raises(ModelProviderUnavailable):
            capability.execute(_valid_input())
        assert gateway.call_count == 1

        gateway2 = FakeStructuredModelGateway(error=ModelGenerationFailed("boom"))
        with pytest.raises(ModelGenerationFailed):
            GeneratePreparationKitCapability(gateway2).execute(_valid_input())


class TestSharedCapabilityId:
    def test_platform_authority(self) -> None:
        assert (
            CAPABILITY_EDUCATION_GENERATE_PREPARATION_KIT
            == "education.generate_preparation_kit"
        )

    def test_i03_imports_shared_constant(self) -> None:
        assert (
            i03_mod.CAPABILITY_EDUCATION_GENERATE_PREPARATION_KIT
            is CAPABILITY_EDUCATION_GENERATE_PREPARATION_KIT
        )
        source = Path(i03_mod.__file__).read_text(encoding="utf-8")
        assert "CAPABILITY_EDUCATION_GENERATE_PREPARATION_KIT =" not in source
        assert "from aieos.platform.capabilities.models import" in source

    def test_not_registered_in_default_registry(self) -> None:
        registry = build_default_capability_registry()
        with pytest.raises(Exception):
            registry.get(CAPABILITY_EDUCATION_GENERATE_PREPARATION_KIT)


class TestArchitectureGuards:
    def test_no_forbidden_provider_sdk_imports_in_education_application(self) -> None:
        for path in _EDUCATION_APP.glob("*.py"):
            imports = _module_imports(path)
            for forbidden in _FORBIDDEN_IMPORTS:
                assert forbidden not in imports, f"{path.name} imports {forbidden}"

    def test_capability_has_no_content_or_generation_run_or_eq(self) -> None:
        source = (
            _EDUCATION_APP / "generate_preparation_kit.py"
        ).read_text(encoding="utf-8")
        lowered = source.lower()
        for forbidden in (
            "createaipreparationartifactsforreview",
            "contentunitofwork",
            "generationrun",
            "evaluate_educational_quality",
            "educationalqualitystatus",
            "openai",
            "temporal",
            "langchain",
            "mcp",
        ):
            assert forbidden not in lowered

    def test_no_new_migration(self) -> None:
        versions = sorted(
            (REPO_ROOT / "migrations" / "versions").glob("*.py"),
            key=lambda p: p.name,
        )
        assert versions
        assert versions[-1].name.startswith("tosd060001_")
        assert not any(p.name.startswith("tosd040002_") for p in versions)

    def test_no_preparation_kit_table_or_generation_artifact_tables(self) -> None:
        for path in (REPO_ROOT / "migrations" / "versions").glob("*.py"):
            text = path.read_text(encoding="utf-8").lower()
            assert "preparation_kit" not in text or "tosd060001" in path.name
            # I04 must not introduce these tables
        i04_sources = [
            _EDUCATION_APP / "generate_preparation_kit.py",
            _EDUCATION_APP / "preparation_artifacts.py",
            _EDUCATION_APP / "preparation_prompt.py",
            _EDUCATION_APP / "models.py",
        ]
        for path in i04_sources:
            text = path.read_text(encoding="utf-8").lower()
            assert "ai.generation_artifacts" not in text
            assert "generation_validated_outputs" not in text
            assert "create table" not in text

    def test_app_factory_untouched_for_runtime_activation(self) -> None:
        factory = (
            REPO_ROOT / "src" / "aieos" / "development" / "app_factory.py"
        ).read_text(encoding="utf-8")
        assert "GeneratePreparationKitCapability" not in factory
        assert "generate_preparation_kit" not in factory

    def test_registry_builder_does_not_register_preparation_kit(self) -> None:
        registry_src = (
            REPO_ROOT / "src" / "aieos" / "platform" / "capabilities" / "registry.py"
        ).read_text(encoding="utf-8")
        assert "GENERATE_PREPARATION" not in registry_src
        assert "education.generate_preparation_kit" not in registry_src

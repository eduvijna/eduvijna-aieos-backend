"""Typed AI generation provenance (GCI-I11). Framework-neutral; no provider SDKs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence, TypeAlias
from uuid import UUID

from aieos.domains.content.domain.errors import InvalidAIGenerationProvenanceError
from aieos.platform.resources import InvalidResourceRefError, ResourceRef

AI_GENERATION_PROVENANCE_KIND = "ai_generation"
AI_GENERATION_PROVENANCE_SCHEMA_VERSION = 1
AI_GENERATION_PROVENANCE_SCHEMA_VERSION_V2 = 2

_TOP_LEVEL_KEYS_V1 = (
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
)
_TOP_LEVEL_KEY_SET_V1 = frozenset(_TOP_LEVEL_KEYS_V1)
_TOP_LEVEL_KEY_SET_V2 = _TOP_LEVEL_KEY_SET_V1 | frozenset({"artifact_kind"})
_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9._-]{0,63}$")
_MODEL_ID_MAX = 255
_RESOURCE_REF_KEYS = frozenset({"resource_type", "resource_id", "resource_revision"})


def _require_identifier(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
        raise InvalidAIGenerationProvenanceError(
            f"{label} must be a stable lowercase identifier"
        )
    return value


def _require_model_id(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise InvalidAIGenerationProvenanceError("model_id must be a non-empty string")
    if len(value.encode("utf-8")) > _MODEL_ID_MAX:
        raise InvalidAIGenerationProvenanceError(
            "model_id must be at most 255 UTF-8 characters"
        )
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise InvalidAIGenerationProvenanceError(
            "model_id must not contain control characters"
        )
    return value


def _resource_ref_as_json(ref: ResourceRef) -> dict[str, object]:
    return {
        "resource_type": ref.resource_type,
        "resource_id": str(ref.resource_id),
        "resource_revision": ref.resource_revision,
    }


def _resource_ref_from_json(value: object, *, label: str) -> ResourceRef:
    if not isinstance(value, Mapping):
        raise InvalidAIGenerationProvenanceError(f"{label} must be a ResourceRef object")
    if set(value.keys()) != _RESOURCE_REF_KEYS:
        raise InvalidAIGenerationProvenanceError(
            f"{label} must contain exactly resource_type, resource_id, resource_revision"
        )
    try:
        resource_id = value["resource_id"]
        if isinstance(resource_id, UUID):
            rid = resource_id
        elif isinstance(resource_id, str):
            rid = UUID(resource_id)
        else:
            raise InvalidAIGenerationProvenanceError(
                f"{label}.resource_id must be a UUID"
            )
        return ResourceRef(
            resource_type=value["resource_type"],  # type: ignore[arg-type]
            resource_id=rid,
            resource_revision=value["resource_revision"],  # type: ignore[arg-type]
        )
    except (KeyError, TypeError, ValueError, InvalidResourceRefError) as exc:
        raise InvalidAIGenerationProvenanceError(f"{label} is not a valid ResourceRef") from exc


def _resource_refs_from_json(value: object, *, label: str) -> tuple[ResourceRef, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise InvalidAIGenerationProvenanceError(f"{label} must be an array")
    return tuple(
        _resource_ref_from_json(item, label=f"{label}[{index}]")
        for index, item in enumerate(value)
    )


def _parse_correlation_id(value: object) -> UUID:
    try:
        if isinstance(value, UUID):
            return value
        if isinstance(value, str):
            return UUID(value)
    except (TypeError, ValueError) as exc:
        raise InvalidAIGenerationProvenanceError(
            "correlation_id must be a UUID"
        ) from exc
    raise InvalidAIGenerationProvenanceError("correlation_id must be a UUID")


def _parse_schema_version(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidAIGenerationProvenanceError("schema_version must be an integer")
    return value


def _validate_exact_keys(
    keys: set[str], *, expected: frozenset[str]
) -> None:
    if keys != expected:
        unexpected = sorted(keys - expected)
        missing = sorted(expected - keys)
        if unexpected:
            raise InvalidAIGenerationProvenanceError(
                f"unexpected provenance field: {unexpected[0]}"
            )
        raise InvalidAIGenerationProvenanceError(
            f"missing provenance field: {missing[0]}"
        )


@dataclass(frozen=True, slots=True)
class AIGenerationProvenanceV1:
    """Allow-listed AI generation provenance. Not authorization truth."""

    generation_run_ref: ResourceRef
    prompt_execution_ref: ResourceRef | None
    provider_id: str
    model_id: str
    capability_id: str
    source_refs: tuple[ResourceRef, ...]
    policy_refs: tuple[ResourceRef, ...]
    evaluation_refs: tuple[ResourceRef, ...]
    correlation_id: UUID

    def __post_init__(self) -> None:
        if not isinstance(self.generation_run_ref, ResourceRef):
            raise InvalidAIGenerationProvenanceError(
                "generation_run_ref must be a ResourceRef"
            )
        if self.prompt_execution_ref is not None and not isinstance(
            self.prompt_execution_ref, ResourceRef
        ):
            raise InvalidAIGenerationProvenanceError(
                "prompt_execution_ref must be a ResourceRef or None"
            )
        object.__setattr__(
            self, "provider_id", _require_identifier(self.provider_id, label="provider_id")
        )
        object.__setattr__(self, "model_id", _require_model_id(self.model_id))
        object.__setattr__(
            self,
            "capability_id",
            _require_identifier(self.capability_id, label="capability_id"),
        )
        for label, refs in (
            ("source_refs", self.source_refs),
            ("policy_refs", self.policy_refs),
            ("evaluation_refs", self.evaluation_refs),
        ):
            if not isinstance(refs, tuple):
                raise InvalidAIGenerationProvenanceError(f"{label} must be a tuple")
            for ref in refs:
                if not isinstance(ref, ResourceRef):
                    raise InvalidAIGenerationProvenanceError(
                        f"{label} entries must be ResourceRef"
                    )
        if not isinstance(self.correlation_id, UUID):
            raise InvalidAIGenerationProvenanceError("correlation_id must be a UUID")

    @property
    def kind(self) -> str:
        return AI_GENERATION_PROVENANCE_KIND

    @property
    def schema_version(self) -> int:
        return AI_GENERATION_PROVENANCE_SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class AIGenerationProvenanceV2:
    """Allow-listed AI generation provenance with artifact_kind (TOS-DEV04)."""

    generation_run_ref: ResourceRef
    prompt_execution_ref: ResourceRef | None
    provider_id: str
    model_id: str
    capability_id: str
    source_refs: tuple[ResourceRef, ...]
    policy_refs: tuple[ResourceRef, ...]
    evaluation_refs: tuple[ResourceRef, ...]
    correlation_id: UUID
    artifact_kind: str

    def __post_init__(self) -> None:
        if not isinstance(self.generation_run_ref, ResourceRef):
            raise InvalidAIGenerationProvenanceError(
                "generation_run_ref must be a ResourceRef"
            )
        if self.prompt_execution_ref is not None and not isinstance(
            self.prompt_execution_ref, ResourceRef
        ):
            raise InvalidAIGenerationProvenanceError(
                "prompt_execution_ref must be a ResourceRef or None"
            )
        object.__setattr__(
            self, "provider_id", _require_identifier(self.provider_id, label="provider_id")
        )
        object.__setattr__(self, "model_id", _require_model_id(self.model_id))
        object.__setattr__(
            self,
            "capability_id",
            _require_identifier(self.capability_id, label="capability_id"),
        )
        object.__setattr__(
            self,
            "artifact_kind",
            _require_identifier(self.artifact_kind, label="artifact_kind"),
        )
        for label, refs in (
            ("source_refs", self.source_refs),
            ("policy_refs", self.policy_refs),
            ("evaluation_refs", self.evaluation_refs),
        ):
            if not isinstance(refs, tuple):
                raise InvalidAIGenerationProvenanceError(f"{label} must be a tuple")
            for ref in refs:
                if not isinstance(ref, ResourceRef):
                    raise InvalidAIGenerationProvenanceError(
                        f"{label} entries must be ResourceRef"
                    )
        if not isinstance(self.correlation_id, UUID):
            raise InvalidAIGenerationProvenanceError("correlation_id must be a UUID")

    @property
    def kind(self) -> str:
        return AI_GENERATION_PROVENANCE_KIND

    @property
    def schema_version(self) -> int:
        return AI_GENERATION_PROVENANCE_SCHEMA_VERSION_V2


AIGenerationProvenance: TypeAlias = AIGenerationProvenanceV1 | AIGenerationProvenanceV2


def _provenance_common_as_json(
    provenance: AIGenerationProvenanceV1 | AIGenerationProvenanceV2,
) -> dict[str, object]:
    return {
        "kind": provenance.kind,
        "schema_version": provenance.schema_version,
        "generation_run_ref": _resource_ref_as_json(provenance.generation_run_ref),
        "prompt_execution_ref": (
            None
            if provenance.prompt_execution_ref is None
            else _resource_ref_as_json(provenance.prompt_execution_ref)
        ),
        "provider_id": provenance.provider_id,
        "model_id": provenance.model_id,
        "capability_id": provenance.capability_id,
        "source_refs": [
            _resource_ref_as_json(ref) for ref in provenance.source_refs
        ],
        "policy_refs": [
            _resource_ref_as_json(ref) for ref in provenance.policy_refs
        ],
        "evaluation_refs": [
            _resource_ref_as_json(ref) for ref in provenance.evaluation_refs
        ],
        "correlation_id": str(provenance.correlation_id),
    }


def ai_generation_provenance_as_json(
    provenance: AIGenerationProvenance,
) -> dict[str, object]:
    """Deterministic allow-listed serialization. No unknown keys."""
    payload = _provenance_common_as_json(provenance)
    if isinstance(provenance, AIGenerationProvenanceV2):
        payload["artifact_kind"] = provenance.artifact_kind
    return payload


def _parse_provenance_v1(value: Mapping[str, Any] | Mapping[str, object]) -> AIGenerationProvenanceV1:
    keys = set(value.keys())
    _validate_exact_keys(keys, expected=_TOP_LEVEL_KEY_SET_V1)
    if value["kind"] != AI_GENERATION_PROVENANCE_KIND:
        raise InvalidAIGenerationProvenanceError("kind must be ai_generation")
    schema_version = _parse_schema_version(value["schema_version"])
    if schema_version != AI_GENERATION_PROVENANCE_SCHEMA_VERSION:
        raise InvalidAIGenerationProvenanceError("schema_version must be 1")
    if value["generation_run_ref"] is None:
        raise InvalidAIGenerationProvenanceError("generation_run_ref is required")
    prompt_raw = value["prompt_execution_ref"]
    prompt_ref = (
        None
        if prompt_raw is None
        else _resource_ref_from_json(prompt_raw, label="prompt_execution_ref")
    )
    return AIGenerationProvenanceV1(
        generation_run_ref=_resource_ref_from_json(
            value["generation_run_ref"], label="generation_run_ref"
        ),
        prompt_execution_ref=prompt_ref,
        provider_id=value["provider_id"],  # type: ignore[arg-type]
        model_id=value["model_id"],  # type: ignore[arg-type]
        capability_id=value["capability_id"],  # type: ignore[arg-type]
        source_refs=_resource_refs_from_json(value["source_refs"], label="source_refs"),
        policy_refs=_resource_refs_from_json(value["policy_refs"], label="policy_refs"),
        evaluation_refs=_resource_refs_from_json(
            value["evaluation_refs"], label="evaluation_refs"
        ),
        correlation_id=_parse_correlation_id(value["correlation_id"]),
    )


def _parse_provenance_v2(value: Mapping[str, Any] | Mapping[str, object]) -> AIGenerationProvenanceV2:
    keys = set(value.keys())
    _validate_exact_keys(keys, expected=_TOP_LEVEL_KEY_SET_V2)
    if value["kind"] != AI_GENERATION_PROVENANCE_KIND:
        raise InvalidAIGenerationProvenanceError("kind must be ai_generation")
    schema_version = _parse_schema_version(value["schema_version"])
    if schema_version != AI_GENERATION_PROVENANCE_SCHEMA_VERSION_V2:
        raise InvalidAIGenerationProvenanceError("schema_version must be 2")
    if value["generation_run_ref"] is None:
        raise InvalidAIGenerationProvenanceError("generation_run_ref is required")
    prompt_raw = value["prompt_execution_ref"]
    prompt_ref = (
        None
        if prompt_raw is None
        else _resource_ref_from_json(prompt_raw, label="prompt_execution_ref")
    )
    return AIGenerationProvenanceV2(
        generation_run_ref=_resource_ref_from_json(
            value["generation_run_ref"], label="generation_run_ref"
        ),
        prompt_execution_ref=prompt_ref,
        provider_id=value["provider_id"],  # type: ignore[arg-type]
        model_id=value["model_id"],  # type: ignore[arg-type]
        capability_id=value["capability_id"],  # type: ignore[arg-type]
        source_refs=_resource_refs_from_json(value["source_refs"], label="source_refs"),
        policy_refs=_resource_refs_from_json(value["policy_refs"], label="policy_refs"),
        evaluation_refs=_resource_refs_from_json(
            value["evaluation_refs"], label="evaluation_refs"
        ),
        correlation_id=_parse_correlation_id(value["correlation_id"]),
        artifact_kind=value["artifact_kind"],  # type: ignore[arg-type]
    )


def ai_generation_provenance_from_json(
    value: Mapping[str, Any] | Mapping[str, object],
) -> AIGenerationProvenance:
    """Strict version-aware parser. Rejects unknown top-level keys and secret-shaped extras."""
    if not isinstance(value, Mapping):
        raise InvalidAIGenerationProvenanceError("provenance must be a JSON object")
    if "schema_version" not in value:
        raise InvalidAIGenerationProvenanceError("missing provenance field: schema_version")
    schema_version = _parse_schema_version(value["schema_version"])
    if schema_version == AI_GENERATION_PROVENANCE_SCHEMA_VERSION:
        return _parse_provenance_v1(value)
    if schema_version == AI_GENERATION_PROVENANCE_SCHEMA_VERSION_V2:
        return _parse_provenance_v2(value)
    raise InvalidAIGenerationProvenanceError(
        f"unsupported provenance schema_version: {schema_version}"
    )

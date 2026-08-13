"""ContentVersion domain contract.

Committed ContentVersion semantics are immutable. Correction means another
ContentVersion, never mutation of the prior version. History baseline is linear.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Mapping
from uuid import UUID

from aieos.domains.content.domain.errors import (
    ContentDomainError,
    ContentVersionImmutabilityError,
    InvalidPayloadError,
    ParentLineageError,
)
from aieos.domains.content.domain.identities import (
    ContentId,
    ContentVersionId,
    VersionNumber,
    require_foreign_uuid,
)
from aieos.domains.content.domain.origin import ContentOrigin, parse_content_origin
from aieos.domains.content.domain.schema import SchemaId, SchemaVersion


def _require_aware(value: datetime, *, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ContentDomainError(f"{label} must be timezone-aware")
    return value


@dataclass(frozen=True, slots=True)
class PayloadSha256:
    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or len(self.value) != 64:
            raise InvalidPayloadError("payload hash must be a 64-character SHA-256 hex digest")
        if any(ch not in "0123456789abcdef" for ch in self.value):
            raise InvalidPayloadError("payload hash must be lowercase hex")

    def __str__(self) -> str:
        return self.value


def freeze_json_value(value: object) -> object:
    """Recursively freeze a JSON-compatible value.

    mappings -> MappingProxyType of a copied dict
    arrays/lists/tuples -> tuple
    Scalars are copied by value. Unsupported Python types are rejected.
    """
    if value is None or isinstance(value, bool) or isinstance(value, str):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise InvalidPayloadError("payload numbers must be finite JSON numbers")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, object] = {}
        for key, nested in value.items():
            if not isinstance(key, str):
                raise InvalidPayloadError("payload object keys must be strings")
            frozen[key] = freeze_json_value(nested)
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(freeze_json_value(item) for item in value)
    raise InvalidPayloadError(
        f"unsupported payload value type {type(value).__name__}"
    )


def thaw_json_value(value: object) -> object:
    """Convert the immutable representation back to normal JSON structures."""
    if isinstance(value, Mapping):
        return {key: thaw_json_value(nested) for key, nested in value.items()}
    if isinstance(value, tuple):
        return [thaw_json_value(item) for item in value]
    return value


def freeze_payload_mapping(body: Mapping[str, object]) -> Mapping[str, object]:
    frozen = freeze_json_value(body)
    if not isinstance(frozen, Mapping):
        raise InvalidPayloadError("payload body must be a JSON object")
    return frozen


@dataclass(frozen=True, slots=True)
class ContentPayload:
    """Deeply immutable domain payload with integrity hash.

    Not a SQL/JSONB persistence mapping. Nested mappings are read-only;
    nested arrays are tuples. No process/module/class-global cache.
    """

    body: Mapping[str, object]
    sha256: PayloadSha256

    def __post_init__(self) -> None:
        if not isinstance(self.body, Mapping):
            raise InvalidPayloadError("payload body must be a mapping")
        frozen_body = freeze_payload_mapping(self.body)
        object.__setattr__(self, "body", frozen_body)
        digest = PayloadSha256(_sha256_hex(canonical_payload_json(frozen_body)))
        if digest != self.sha256:
            raise InvalidPayloadError("payload sha256 does not match canonical body")

    @classmethod
    def from_mapping(cls, body: Mapping[str, object]) -> ContentPayload:
        frozen_body = freeze_payload_mapping(body)
        digest = PayloadSha256(_sha256_hex(canonical_payload_json(frozen_body)))
        return cls(body=frozen_body, sha256=digest)


def _sha256_hex(canonical: str) -> str:
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def canonical_payload_json(body: Mapping[str, object]) -> str:
    try:
        return json.dumps(
            thaw_json_value(body),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise InvalidPayloadError("payload body is not JSON-canonical") from exc


@dataclass(frozen=True, slots=True)
class ContentVersion:
    version_id: ContentVersionId
    tenant_id: UUID
    content_id: ContentId
    version_number: VersionNumber
    parent_version_id: ContentVersionId | None
    schema_id: SchemaId
    schema_version: SchemaVersion
    payload: ContentPayload
    origin: ContentOrigin
    created_at: datetime
    created_by_principal_id: UUID

    def __post_init__(self) -> None:
        object.__setattr__(self, "origin", parse_content_origin(self.origin))
        require_foreign_uuid(self.tenant_id, label="tenant_id")
        require_foreign_uuid(
            self.created_by_principal_id, label="created_by_principal_id"
        )
        _require_aware(self.created_at, label="created_at")
        is_first = self.version_number.value == 1
        has_parent = self.parent_version_id is not None
        if is_first == has_parent:
            raise ParentLineageError(
                "version_number == 1 if and only if parent_version_id is None"
            )
        if self.parent_version_id == self.version_id:
            raise ParentLineageError("parent_version_id must not equal version_id")

    def assert_unmutated_relative_to(self, other: ContentVersion) -> None:
        """Pairwise comparison of two explicitly supplied ContentVersion values.

        Does not consult process/module/class-global state. Authoritative
        uniqueness of committed history is deferred to persistence slices.
        """
        if self.version_id != other.version_id:
            return
        if self != other:
            raise ContentVersionImmutabilityError(
                "committed ContentVersion cannot change in place; "
                "correction requires a new ContentVersion"
            )


def validate_linear_parent(child: ContentVersion, parent: ContentVersion) -> None:
    """Parent, when provided, must belong to the same tenant + Content aggregate.

    History baseline is linear: child's version_number is parent + 1 and
    child.parent_version_id equals parent.version_id.
    """
    if child.parent_version_id is None:
        raise ParentLineageError("child ContentVersion has no parent_version_id")
    if parent.tenant_id != child.tenant_id:
        raise ParentLineageError("parent version must belong to the same tenant")
    if parent.content_id != child.content_id:
        raise ParentLineageError("parent version must belong to the same Content aggregate")
    if parent.version_id != child.parent_version_id:
        raise ParentLineageError("parent_version_id must reference the provided parent")
    if child.version_number.value != parent.version_number.value + 1:
        raise ParentLineageError(
            "linear history requires version_number == parent.version_number + 1"
        )
    if parent.parent_version_id == child.version_id:
        raise ParentLineageError("cyclic parent lineage is not permitted")

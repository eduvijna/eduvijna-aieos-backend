"""Code-controlled Content type / schema registry contracts.

The registry is not a database, admin UI, or plugin marketplace.
Historical schema versions remain resolvable so old ContentVersions stay readable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol

from aieos.domains.content.domain.errors import (
    DuplicateSchemaVersionError,
    InvalidContentTypeError,
    SchemaNotFoundError,
)


@dataclass(frozen=True, slots=True)
class SchemaId:
    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not self.value.strip():
            raise InvalidContentTypeError("schema_id must be a non-empty string")
        object.__setattr__(self, "value", self.value.strip())

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class SchemaVersion:
    value: int

    def __post_init__(self) -> None:
        if isinstance(self.value, bool) or not isinstance(self.value, int) or self.value < 1:
            raise InvalidContentTypeError("schema_version must be a positive integer")

    def __int__(self) -> int:
        return self.value


@dataclass(frozen=True, slots=True)
class SchemaRef:
    schema_id: SchemaId
    schema_version: SchemaVersion


class ContentSchema(Protocol):
    """Validator/resolution contract for one historical schema version."""

    @property
    def content_type(self) -> str: ...

    @property
    def schema_id(self) -> SchemaId: ...

    @property
    def schema_version(self) -> SchemaVersion: ...

    def validate(self, payload: Mapping[str, object]) -> None: ...


@dataclass(frozen=True, slots=True)
class RegisteredSchema:
    content_type: str
    schema_id: SchemaId
    schema_version: SchemaVersion
    validator: ContentSchema

    def validate(self, payload: Mapping[str, object]) -> None:
        self.validator.validate(payload)


class ContentSchemaRegistry:
    """In-memory, code-controlled schema registry.

    Multiple historical versions of the same schema_id coexist.
    Registering a newer version does not remove older versions.
    """

    def __init__(self) -> None:
        self._by_id_version: dict[tuple[str, int], RegisteredSchema] = {}

    def register(self, schema: ContentSchema) -> None:
        key = (str(schema.schema_id), int(schema.schema_version))
        if key in self._by_id_version:
            raise DuplicateSchemaVersionError(
                f"schema {key[0]!r} version {key[1]} is already registered"
            )
        self._by_id_version[key] = RegisteredSchema(
            content_type=schema.content_type,
            schema_id=schema.schema_id,
            schema_version=schema.schema_version,
            validator=schema,
        )

    def get(self, schema_id: SchemaId | str, schema_version: SchemaVersion | int) -> RegisteredSchema:
        sid = schema_id.value if isinstance(schema_id, SchemaId) else SchemaId(schema_id).value
        sver = (
            schema_version.value
            if isinstance(schema_version, SchemaVersion)
            else SchemaVersion(schema_version).value
        )
        try:
            return self._by_id_version[(sid, sver)]
        except KeyError as exc:
            raise SchemaNotFoundError(
                f"schema {sid!r} version {sver} is not registered"
            ) from exc

    def list_versions(self, schema_id: SchemaId | str) -> tuple[int, ...]:
        sid = schema_id.value if isinstance(schema_id, SchemaId) else SchemaId(schema_id).value
        versions = sorted(ver for (key, ver) in self._by_id_version if key == sid)
        if not versions:
            raise SchemaNotFoundError(f"schema {sid!r} has no registered versions")
        return tuple(versions)

    def resolve(
        self, schema_id: SchemaId | str, schema_version: SchemaVersion | int
    ) -> RegisteredSchema:
        return self.get(schema_id, schema_version)

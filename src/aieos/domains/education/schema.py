"""ContentSchema adapter for education.worksheet@1."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from pydantic import ValidationError

from aieos.domains.content.domain.errors import InvalidPayloadError
from aieos.domains.content.domain.schema import SchemaId, SchemaVersion
from aieos.domains.education.worksheet_v1 import WorksheetV1

WORKSHEET_CONTENT_TYPE = "worksheet"
WORKSHEET_SCHEMA_ID = "education.worksheet"
WORKSHEET_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class WorksheetV1ContentSchema:
    """Validates worksheet payloads via WorksheetV1 (rejects unknown fields)."""

    content_type: str = WORKSHEET_CONTENT_TYPE
    schema_id: SchemaId = SchemaId(WORKSHEET_SCHEMA_ID)
    schema_version: SchemaVersion = SchemaVersion(WORKSHEET_SCHEMA_VERSION)

    def validate(self, payload: Mapping[str, object]) -> None:
        if not isinstance(payload, Mapping):
            raise InvalidPayloadError("worksheet payload must be a JSON object")
        try:
            WorksheetV1.model_validate(dict(payload))
        except ValidationError as exc:
            raise InvalidPayloadError(f"worksheet payload invalid: {exc.error_count()} error(s)") from exc


WORKSHEET_V1_SCHEMA = WorksheetV1ContentSchema()

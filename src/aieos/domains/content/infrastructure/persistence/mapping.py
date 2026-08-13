"""Infrastructure conversion between domain objects and SQL rows."""

from __future__ import annotations

from typing import Any, Mapping

from aieos.domains.content.domain.content import Content, ContentType
from aieos.domains.content.domain.identities import (
    AggregateRevision,
    ContentId,
    ContentVersionId,
    VersionNumber,
)
from aieos.domains.content.domain.origin import parse_content_origin
from aieos.domains.content.domain.schema import SchemaId, SchemaVersion
from aieos.domains.content.domain.states import parse_stewardship_state
from aieos.domains.content.domain.version import (
    ContentPayload,
    ContentVersion,
    PayloadSha256,
    thaw_json_value,
)


def payload_as_json(version: ContentVersion) -> dict[str, Any]:
    thawed = thaw_json_value(version.payload.body)
    if not isinstance(thawed, dict):
        raise TypeError("ContentPayload body must thaw to a JSON object")
    return thawed


def provenance_as_json(provenance: Mapping[str, object] | None) -> dict[str, Any] | None:
    if provenance is None:
        return None
    thawed = thaw_json_value(dict(provenance))
    if not isinstance(thawed, dict):
        raise TypeError("provenance must be a JSON object")
    return thawed


def _row_value(row: Any, name: str) -> Any:
    if isinstance(row, Mapping):
        return row[name]
    return getattr(row, name)


def content_from_row(row: Any) -> Content:
    current = _row_value(row, "current_version_id")
    published = _row_value(row, "published_version_id")
    return Content(
        content_id=ContentId(_row_value(row, "content_id")),
        tenant_id=_row_value(row, "tenant_id"),
        owner_principal_id=_row_value(row, "owner_principal_id"),
        content_type=ContentType(_row_value(row, "content_type")),
        title=_row_value(row, "title"),
        description=_row_value(row, "description"),
        locale=_row_value(row, "locale"),
        stewardship_state=parse_stewardship_state(_row_value(row, "stewardship_state")),
        current_version_id=None if current is None else ContentVersionId(current),
        published_version_id=None if published is None else ContentVersionId(published),
        aggregate_revision=AggregateRevision(int(_row_value(row, "aggregate_revision"))),
        created_at=_row_value(row, "created_at"),
        created_by_principal_id=_row_value(row, "created_by_principal_id"),
        updated_at=_row_value(row, "updated_at"),
        archived_at=_row_value(row, "archived_at"),
    )


def content_version_from_row(row: Any) -> ContentVersion:
    parent = row.parent_version_id
    return ContentVersion(
        version_id=ContentVersionId(row.version_id),
        tenant_id=row.tenant_id,
        content_id=ContentId(row.content_id),
        version_number=VersionNumber(int(row.version_number)),
        parent_version_id=None if parent is None else ContentVersionId(parent),
        schema_id=SchemaId(row.schema_id),
        schema_version=SchemaVersion(int(row.schema_version)),
        payload=ContentPayload(
            body=row.payload,
            sha256=PayloadSha256(row.payload_sha256),
        ),
        origin=parse_content_origin(row.origin),
        created_at=row.created_at,
        created_by_principal_id=row.created_by_principal_id,
    )

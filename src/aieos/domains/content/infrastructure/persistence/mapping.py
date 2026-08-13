"""Infrastructure conversion between domain ContentVersion and SQL rows."""

from __future__ import annotations

from typing import Any, Mapping

from aieos.domains.content.domain.identities import ContentId, ContentVersionId, VersionNumber
from aieos.domains.content.domain.origin import parse_content_origin
from aieos.domains.content.domain.schema import SchemaId, SchemaVersion
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

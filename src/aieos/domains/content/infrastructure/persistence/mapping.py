"""Infrastructure conversion between domain objects and SQL rows."""

from __future__ import annotations

from typing import Any, Mapping

from aieos.domains.content.domain.content import Content, ContentType
from aieos.domains.content.domain.identities import (
    AggregateRevision,
    ContentId,
    ContentVersionId,
    PublicationId,
    ReviewDecisionId,
    VersionNumber,
)
from aieos.domains.content.domain.origin import parse_content_origin
from aieos.domains.content.domain.publication import Publication
from aieos.domains.content.domain.review import ReviewDecision, parse_review_decision_code
from aieos.domains.content.domain.schema import SchemaId, SchemaVersion
from aieos.domains.content.domain.states import parse_stewardship_state
from aieos.domains.content.domain.version import (
    ContentPayload,
    ContentVersion,
    PayloadSha256,
    thaw_json_value,
)
from aieos.domains.content.domain.version_asset_ref import VersionAssetRef
from aieos.platform.resources import ResourceRef


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


def review_decision_from_row(row: Any) -> ReviewDecision:
    return ReviewDecision(
        review_decision_id=ReviewDecisionId(_row_value(row, "review_decision_id")),
        tenant_id=_row_value(row, "tenant_id"),
        content_id=ContentId(_row_value(row, "content_id")),
        version_id=ContentVersionId(_row_value(row, "version_id")),
        decision=parse_review_decision_code(_row_value(row, "decision")),
        reason_code=_row_value(row, "reason_code"),
        comment=_row_value(row, "comment"),
        reviewer_principal_id=_row_value(row, "reviewer_principal_id"),
        effective_actor_id=_row_value(row, "effective_actor_id"),
        delegation_id=_row_value(row, "delegation_id"),
        decided_at=_row_value(row, "decided_at"),
        correlation_id=_row_value(row, "correlation_id"),
    )


def publication_from_row(row: Any) -> Publication:
    return Publication(
        publication_id=PublicationId(_row_value(row, "publication_id")),
        tenant_id=_row_value(row, "tenant_id"),
        content_id=ContentId(_row_value(row, "content_id")),
        version_id=ContentVersionId(_row_value(row, "version_id")),
        approval_decision_id=ReviewDecisionId(_row_value(row, "approval_decision_id")),
        published_by_principal_id=_row_value(row, "published_by_principal_id"),
        effective_actor_id=_row_value(row, "effective_actor_id"),
        published_at=_row_value(row, "published_at"),
        correlation_id=_row_value(row, "correlation_id"),
    )


def version_asset_ref_from_row(row: Any) -> VersionAssetRef:
    revision = _row_value(row, "asset_resource_revision")
    return VersionAssetRef(
        tenant_id=_row_value(row, "tenant_id"),
        content_id=ContentId(_row_value(row, "content_id")),
        version_id=ContentVersionId(_row_value(row, "version_id")),
        resource_ref=ResourceRef(
            resource_type=_row_value(row, "asset_resource_type"),
            resource_id=_row_value(row, "asset_resource_id"),
            resource_revision=None if revision is None else int(revision),
        ),
        role=_row_value(row, "role"),
        ordinal=int(_row_value(row, "ordinal")),
        required=bool(_row_value(row, "required")),
        created_at=_row_value(row, "created_at"),
    )

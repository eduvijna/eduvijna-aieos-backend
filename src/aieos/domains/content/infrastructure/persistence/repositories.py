"""SQLAlchemy Core repositories. They never commit or rollback."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, or_, select, update
from sqlalchemy.engine import Connection

from aieos.domains.content.application.errors import (
    ContentAlreadyExists,
    ContentVersionAlreadyPublished,
    PersistenceInvariantViolation,
    ReviewAlreadyDecided,
    VersionAlreadyExists,
)
from aieos.domains.content.application.models import LockedContentHead
from aieos.domains.content.application.review_queue_models import (
    ARTIFACT_STATUS_IN_REVIEW,
    TeacherReviewQueueDetail,
    TeacherReviewQueueItem,
)
from aieos.domains.content.domain.content import Content
from aieos.domains.content.domain.identities import (
    AggregateRevision,
    ContentId,
    ContentVersionId,
    PublicationId,
    ReviewDecisionId,
    VersionNumber,
)
from aieos.domains.content.domain.publication import Publication
from aieos.domains.content.domain.review import ReviewDecision
from aieos.domains.content.domain.version import ContentVersion, thaw_json_value
from aieos.domains.content.domain.version_asset_ref import VersionAssetRef
from aieos.domains.content.infrastructure.persistence.errors import (
    reraise_as_application_error,
)
from aieos.domains.content.infrastructure.persistence.mapping import (
    content_from_row,
    content_version_from_row,
    payload_as_json,
    provenance_as_json,
    publication_from_row,
    review_decision_from_row,
    version_asset_ref_from_row,
)
from aieos.domains.content.infrastructure.persistence.models import (
    content_versions_table,
    contents_table,
    publications_table,
    review_decisions_table,
    version_asset_refs_table,
)


class SqlAlchemyContentVersionRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(
        self,
        version: ContentVersion,
        provenance: Mapping[str, object] | None,
    ) -> None:
        parent = version.parent_version_id
        values: dict[str, object] = {
            "version_id": version.version_id.value,
            "tenant_id": version.tenant_id,
            "content_id": version.content_id.value,
            "version_number": version.version_number.value,
            "parent_version_id": None if parent is None else parent.value,
            "schema_id": str(version.schema_id),
            "schema_version": version.schema_version.value,
            "payload": payload_as_json(version),
            "payload_sha256": version.payload.sha256.value,
            "origin": version.origin.value,
            "created_at": version.created_at,
            "created_by_principal_id": version.created_by_principal_id,
        }
        if provenance is not None:
            values["provenance"] = provenance_as_json(provenance)
        try:
            self._connection.execute(content_versions_table.insert().values(**values))
        except Exception as exc:
            reraise_as_application_error(
                exc,
                unique_conflict=VersionAlreadyExists,
                unique_message="ContentVersion identity or version_number already exists",
            )

    def get(self, version_id: ContentVersionId) -> ContentVersion | None:
        try:
            row = self._connection.execute(
                select(content_versions_table).where(
                    content_versions_table.c.version_id == version_id.value
                )
            ).mappings().one_or_none()
            if row is None:
                return None
            return content_version_from_row(row)
        except Exception as exc:
            reraise_as_application_error(exc)

    def get_provenance(
        self, version_id: ContentVersionId
    ) -> Mapping[str, object] | None:
        try:
            value = self._connection.execute(
                select(content_versions_table.c.provenance).where(
                    content_versions_table.c.version_id == version_id.value
                )
            ).scalar_one_or_none()
        except Exception as exc:
            reraise_as_application_error(exc)
        if value is None:
            return None
        if not isinstance(value, Mapping):
            raise PersistenceInvariantViolation("stored provenance must be a JSON object")
        return dict(value)


class SqlAlchemyContentRepository:
    def __init__(self, connection: Connection, execution_tenant_id: UUID) -> None:
        self._connection = connection
        self._execution_tenant_id = execution_tenant_id

    def insert(self, content: Content) -> None:
        values: dict[str, object] = {
            "content_id": content.content_id.value,
            "tenant_id": content.tenant_id,
            "owner_principal_id": content.owner_principal_id,
            "content_type": content.content_type.value,
            "title": content.title,
            "description": content.description,
            "locale": content.locale,
            "stewardship_state": content.stewardship_state.value,
            "current_version_id": (
                None
                if content.current_version_id is None
                else content.current_version_id.value
            ),
            "published_version_id": (
                None
                if content.published_version_id is None
                else content.published_version_id.value
            ),
            "aggregate_revision": content.aggregate_revision.value,
            "created_at": content.created_at,
            "created_by_principal_id": content.created_by_principal_id,
            "updated_at": content.updated_at,
            "archived_at": content.archived_at,
        }
        try:
            self._connection.execute(contents_table.insert().values(**values))
        except Exception as exc:
            reraise_as_application_error(
                exc,
                unique_conflict=ContentAlreadyExists,
                unique_message="Content identity already exists",
            )

    def get(self, content_id: ContentId) -> Content | None:
        try:
            row = (
                self._connection.execute(
                    select(contents_table).where(
                        contents_table.c.content_id == content_id.value,
                        contents_table.c.tenant_id == self._execution_tenant_id,
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                return None
            return content_from_row(row)
        except Exception as exc:
            reraise_as_application_error(exc)

    def list_page(
        self,
        *,
        limit: int,
        after_created_at: datetime | None,
        after_content_id: ContentId | None,
    ) -> list[Content]:
        stmt = (
            select(contents_table)
            .where(contents_table.c.tenant_id == self._execution_tenant_id)
            .order_by(
                contents_table.c.created_at.desc(),
                contents_table.c.content_id.desc(),
            )
            .limit(limit)
        )
        if after_created_at is not None and after_content_id is not None:
            stmt = stmt.where(
                or_(
                    contents_table.c.created_at < after_created_at,
                    and_(
                        contents_table.c.created_at == after_created_at,
                        contents_table.c.content_id < after_content_id.value,
                    ),
                )
            )
        try:
            rows = self._connection.execute(stmt).mappings().all()
            return [content_from_row(row) for row in rows]
        except Exception as exc:
            reraise_as_application_error(exc)

    def get_head_for_update(self, content_id: ContentId) -> LockedContentHead | None:
        stmt = (
            select(
                contents_table.c.tenant_id,
                contents_table.c.content_id,
                contents_table.c.aggregate_revision,
                contents_table.c.current_version_id,
                contents_table.c.published_version_id,
                contents_table.c.stewardship_state,
                contents_table.c.content_type,
                content_versions_table.c.version_number,
            )
            .select_from(
                contents_table.outerjoin(
                    content_versions_table,
                    (
                        content_versions_table.c.tenant_id == contents_table.c.tenant_id
                    )
                    & (
                        content_versions_table.c.content_id
                        == contents_table.c.content_id
                    )
                    & (
                        content_versions_table.c.version_id
                        == contents_table.c.current_version_id
                    ),
                )
            )
            .where(
                contents_table.c.content_id == content_id.value,
                contents_table.c.tenant_id == self._execution_tenant_id,
            )
            .with_for_update(of=contents_table)
        )
        try:
            row = self._connection.execute(stmt).one_or_none()
        except Exception as exc:
            reraise_as_application_error(exc)
        if row is None:
            return None
        current_id = row.current_version_id
        current_number = row.version_number
        published = row.published_version_id
        return LockedContentHead(
            tenant_id=row.tenant_id,
            content_id=ContentId(row.content_id),
            aggregate_revision=AggregateRevision(int(row.aggregate_revision)),
            current_version_id=(
                None if current_id is None else ContentVersionId(current_id)
            ),
            current_version_number=(
                None if current_number is None else VersionNumber(int(current_number))
            ),
            published_version_id=(
                None if published is None else ContentVersionId(published)
            ),
            stewardship_state=row.stewardship_state,
            content_type=row.content_type,
        )

    def advance_current_version(
        self,
        *,
        content_id: ContentId,
        tenant_id: UUID,
        expected_revision: AggregateRevision,
        expected_current_version_id: ContentVersionId | None,
        expected_state: str,
        new_version_id: ContentVersionId,
        updated_at: datetime,
    ) -> AggregateRevision | None:
        expected_current = (
            None
            if expected_current_version_id is None
            else expected_current_version_id.value
        )
        stmt = (
            update(contents_table)
            .where(
                contents_table.c.tenant_id == tenant_id,
                contents_table.c.content_id == content_id.value,
                contents_table.c.aggregate_revision == expected_revision.value,
                contents_table.c.current_version_id.is_not_distinct_from(
                    expected_current
                ),
                contents_table.c.stewardship_state == expected_state,
            )
            .values(
                current_version_id=new_version_id.value,
                aggregate_revision=contents_table.c.aggregate_revision + 1,
                updated_at=updated_at,
                stewardship_state="GENERATED",
            )
            .returning(contents_table.c.aggregate_revision)
        )
        try:
            row = self._connection.execute(stmt).one_or_none()
        except Exception as exc:
            reraise_as_application_error(exc)
        if row is None:
            return None
        return AggregateRevision(int(row.aggregate_revision))

    def transition_stewardship(
        self,
        *,
        content_id: ContentId,
        tenant_id: UUID,
        expected_revision: AggregateRevision,
        expected_current_version_id: ContentVersionId,
        expected_state: str,
        target_state: str,
        updated_at: datetime,
    ) -> AggregateRevision | None:
        stmt = (
            update(contents_table)
            .where(
                contents_table.c.tenant_id == tenant_id,
                contents_table.c.content_id == content_id.value,
                contents_table.c.current_version_id == expected_current_version_id.value,
                contents_table.c.stewardship_state == expected_state,
                contents_table.c.aggregate_revision == expected_revision.value,
            )
            .values(
                stewardship_state=target_state,
                aggregate_revision=contents_table.c.aggregate_revision + 1,
                updated_at=updated_at,
            )
            .returning(contents_table.c.aggregate_revision)
        )
        try:
            row = self._connection.execute(stmt).one_or_none()
        except Exception as exc:
            reraise_as_application_error(exc)
        if row is None:
            return None
        return AggregateRevision(int(row.aggregate_revision))

    def set_published_version(
        self,
        *,
        content_id: ContentId,
        tenant_id: UUID,
        version_id: ContentVersionId,
        expected_revision: AggregateRevision,
        updated_at: datetime,
    ) -> AggregateRevision | None:
        stmt = (
            update(contents_table)
            .where(
                contents_table.c.tenant_id == tenant_id,
                contents_table.c.content_id == content_id.value,
                contents_table.c.current_version_id == version_id.value,
                contents_table.c.stewardship_state == "APPROVED",
                contents_table.c.aggregate_revision == expected_revision.value,
                contents_table.c.published_version_id.is_distinct_from(version_id.value),
            )
            .values(
                published_version_id=version_id.value,
                aggregate_revision=contents_table.c.aggregate_revision + 1,
                updated_at=updated_at,
            )
            .returning(contents_table.c.aggregate_revision)
        )
        try:
            row = self._connection.execute(stmt).one_or_none()
        except Exception as exc:
            reraise_as_application_error(exc)
        if row is None:
            return None
        return AggregateRevision(int(row.aggregate_revision))


class SqlAlchemyReviewDecisionRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, decision: ReviewDecision) -> None:
        try:
            self._connection.execute(
                review_decisions_table.insert().values(
                    review_decision_id=decision.review_decision_id.value,
                    tenant_id=decision.tenant_id,
                    content_id=decision.content_id.value,
                    version_id=decision.version_id.value,
                    decision=decision.decision.value,
                    reason_code=decision.reason_code,
                    comment=decision.comment,
                    reviewer_principal_id=decision.reviewer_principal_id,
                    effective_actor_id=decision.effective_actor_id,
                    delegation_id=decision.delegation_id,
                    decided_at=decision.decided_at,
                    correlation_id=decision.correlation_id,
                )
            )
        except Exception as exc:
            reraise_as_application_error(
                exc,
                unique_conflict=ReviewAlreadyDecided,
                unique_message="this ContentVersion already has a terminal ReviewDecision",
            )

    def get(self, review_decision_id: ReviewDecisionId) -> ReviewDecision | None:
        try:
            row = (
                self._connection.execute(
                    select(review_decisions_table).where(
                        review_decisions_table.c.review_decision_id
                        == review_decision_id.value
                    )
                )
                .mappings()
                .one_or_none()
            )
        except Exception as exc:
            reraise_as_application_error(exc)
        if row is None:
            return None
        return review_decision_from_row(row)

    def get_for_version(
        self, content_id: ContentId, version_id: ContentVersionId
    ) -> ReviewDecision | None:
        try:
            row = (
                self._connection.execute(
                    select(review_decisions_table).where(
                        review_decisions_table.c.content_id == content_id.value,
                        review_decisions_table.c.version_id == version_id.value,
                    )
                )
                .mappings()
                .one_or_none()
            )
        except Exception as exc:
            reraise_as_application_error(exc)
        if row is None:
            return None
        return review_decision_from_row(row)

class SqlAlchemyPublicationRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, publication: Publication) -> None:
        try:
            self._connection.execute(
                publications_table.insert().values(
                    publication_id=publication.publication_id.value,
                    tenant_id=publication.tenant_id,
                    content_id=publication.content_id.value,
                    version_id=publication.version_id.value,
                    approval_decision_id=publication.approval_decision_id.value,
                    published_by_principal_id=publication.published_by_principal_id,
                    effective_actor_id=publication.effective_actor_id,
                    published_at=publication.published_at,
                    correlation_id=publication.correlation_id,
                )
            )
        except Exception as exc:
            reraise_as_application_error(
                exc,
                unique_conflict=ContentVersionAlreadyPublished,
                unique_message="this ContentVersion already has a Publication",
            )

    def get(self, publication_id: PublicationId) -> Publication | None:
        try:
            row = (
                self._connection.execute(
                    select(publications_table).where(
                        publications_table.c.publication_id == publication_id.value
                    )
                )
                .mappings()
                .one_or_none()
            )
        except Exception as exc:
            reraise_as_application_error(exc)
        if row is None:
            return None
        return publication_from_row(row)

    def get_for_version(
        self, content_id: ContentId, version_id: ContentVersionId
    ) -> Publication | None:
        try:
            row = (
                self._connection.execute(
                    select(publications_table).where(
                        publications_table.c.content_id == content_id.value,
                        publications_table.c.version_id == version_id.value,
                    )
                )
                .mappings()
                .one_or_none()
            )
        except Exception as exc:
            reraise_as_application_error(exc)
        if row is None:
            return None
        return publication_from_row(row)


class SqlAlchemyVersionAssetRefRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert_many(self, refs: Sequence[VersionAssetRef]) -> None:
        if not refs:
            return
        values = [
            {
                "tenant_id": ref.tenant_id,
                "content_id": ref.content_id.value,
                "version_id": ref.version_id.value,
                "asset_resource_type": ref.resource_ref.resource_type,
                "asset_resource_id": ref.resource_ref.resource_id,
                "asset_resource_revision": ref.resource_ref.resource_revision,
                "role": ref.role,
                "ordinal": ref.ordinal,
                "required": ref.required,
                "created_at": ref.created_at,
            }
            for ref in refs
        ]
        try:
            self._connection.execute(version_asset_refs_table.insert(), values)
        except Exception as exc:
            reraise_as_application_error(exc)

    def list_for_version(
        self, content_id: ContentId, version_id: ContentVersionId
    ) -> list[VersionAssetRef]:
        try:
            rows = (
                self._connection.execute(
                    select(version_asset_refs_table)
                    .where(
                        version_asset_refs_table.c.content_id == content_id.value,
                        version_asset_refs_table.c.version_id == version_id.value,
                    )
                    .order_by(
                        version_asset_refs_table.c.role.asc(),
                        version_asset_refs_table.c.ordinal.asc(),
                        version_asset_refs_table.c.asset_resource_type.asc(),
                        version_asset_refs_table.c.asset_resource_id.asc(),
                    )
                )
                .mappings()
                .all()
            )
        except Exception as exc:
            reraise_as_application_error(exc)
        return [version_asset_ref_from_row(row) for row in rows]


def _queue_item_from_row(row: Mapping[str, object]) -> TeacherReviewQueueItem:
    published = row["published_version_id"]
    return TeacherReviewQueueItem(
        content_id=ContentId(row["content_id"]),  # type: ignore[arg-type]
        version_id=ContentVersionId(row["version_id"]),  # type: ignore[arg-type]
        version_number=VersionNumber(int(row["version_number"])),  # type: ignore[arg-type]
        content_type=str(row["content_type"]),
        title=str(row["title"]),
        description=str(row["description"]),
        locale=str(row["locale"]),
        artifact_status=ARTIFACT_STATUS_IN_REVIEW,
        origin=str(row["origin"]),
        aggregate_revision=AggregateRevision(int(row["aggregate_revision"])),  # type: ignore[arg-type]
        submitted_at=row["submitted_at"],  # type: ignore[arg-type]
        version_created_at=row["version_created_at"],  # type: ignore[arg-type]
        published_version_id=(
            None if published is None else ContentVersionId(published)  # type: ignore[arg-type]
        ),
    )


class SqlAlchemyReviewQueueReadRepository:
    """Read-only projection. No insert/update/delete/commit/rollback."""

    def __init__(self, connection: Connection, execution_tenant_id: UUID) -> None:
        self._connection = connection
        self._execution_tenant_id = execution_tenant_id

    def _eligible_join(self):
        decision_exists = (
            select(review_decisions_table.c.review_decision_id)
            .where(
                review_decisions_table.c.tenant_id == contents_table.c.tenant_id,
                review_decisions_table.c.content_id == contents_table.c.content_id,
                review_decisions_table.c.version_id
                == contents_table.c.current_version_id,
            )
            .exists()
        )
        return (
            contents_table.join(
                content_versions_table,
                and_(
                    content_versions_table.c.tenant_id == contents_table.c.tenant_id,
                    content_versions_table.c.content_id == contents_table.c.content_id,
                    content_versions_table.c.version_id
                    == contents_table.c.current_version_id,
                ),
            ),
            decision_exists,
        )

    def list_page(
        self,
        *,
        limit: int,
        after_submitted_at: datetime | None,
        after_content_id: ContentId | None,
    ) -> list[TeacherReviewQueueItem]:
        join_from, decision_exists = self._eligible_join()
        stmt = (
            select(
                contents_table.c.content_id,
                contents_table.c.content_type,
                contents_table.c.title,
                contents_table.c.description,
                contents_table.c.locale,
                contents_table.c.aggregate_revision,
                contents_table.c.updated_at.label("submitted_at"),
                contents_table.c.published_version_id,
                content_versions_table.c.version_id,
                content_versions_table.c.version_number,
                content_versions_table.c.origin,
                content_versions_table.c.created_at.label("version_created_at"),
            )
            .select_from(join_from)
            .where(
                contents_table.c.tenant_id == self._execution_tenant_id,
                contents_table.c.stewardship_state == "IN_REVIEW",
                contents_table.c.current_version_id.is_not(None),
                ~decision_exists,
            )
            .order_by(
                contents_table.c.updated_at.asc(),
                contents_table.c.content_id.asc(),
            )
            .limit(limit)
        )
        if after_submitted_at is not None and after_content_id is not None:
            stmt = stmt.where(
                or_(
                    contents_table.c.updated_at > after_submitted_at,
                    and_(
                        contents_table.c.updated_at == after_submitted_at,
                        contents_table.c.content_id > after_content_id.value,
                    ),
                )
            )
        try:
            rows = self._connection.execute(stmt).mappings().all()
            return [_queue_item_from_row(row) for row in rows]
        except Exception as exc:
            reraise_as_application_error(exc)

    def get_item(
        self, content_id: ContentId, version_id: ContentVersionId
    ) -> TeacherReviewQueueDetail | None:
        join_from, decision_exists = self._eligible_join()
        stmt = (
            select(
                contents_table.c.content_id,
                contents_table.c.content_type,
                contents_table.c.title,
                contents_table.c.description,
                contents_table.c.locale,
                contents_table.c.aggregate_revision,
                contents_table.c.updated_at.label("submitted_at"),
                contents_table.c.published_version_id,
                content_versions_table.c.version_id,
                content_versions_table.c.version_number,
                content_versions_table.c.origin,
                content_versions_table.c.created_at.label("version_created_at"),
                content_versions_table.c.schema_id,
                content_versions_table.c.schema_version,
                content_versions_table.c.payload,
                content_versions_table.c.payload_sha256,
            )
            .select_from(join_from)
            .where(
                contents_table.c.tenant_id == self._execution_tenant_id,
                contents_table.c.content_id == content_id.value,
                contents_table.c.stewardship_state == "IN_REVIEW",
                contents_table.c.current_version_id == version_id.value,
                content_versions_table.c.version_id == version_id.value,
                ~decision_exists,
            )
            .limit(1)
        )
        try:
            row = self._connection.execute(stmt).mappings().one_or_none()
        except Exception as exc:
            reraise_as_application_error(exc)
        if row is None:
            return None
        item = _queue_item_from_row(row)
        thawed = thaw_json_value(row["payload"])
        if not isinstance(thawed, Mapping):
            raise PersistenceInvariantViolation(
                "ContentVersion payload must be a JSON object"
            )
        return TeacherReviewQueueDetail(
            content_id=item.content_id,
            version_id=item.version_id,
            version_number=item.version_number,
            content_type=item.content_type,
            title=item.title,
            description=item.description,
            locale=item.locale,
            artifact_status=item.artifact_status,
            origin=item.origin,
            aggregate_revision=item.aggregate_revision,
            submitted_at=item.submitted_at,
            version_created_at=item.version_created_at,
            published_version_id=item.published_version_id,
            schema_id=str(row["schema_id"]),
            schema_version=int(row["schema_version"]),  # type: ignore[arg-type]
            payload=thawed,
            payload_sha256=str(row["payload_sha256"]),
        )
